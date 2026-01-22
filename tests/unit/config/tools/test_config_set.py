"""Unit tests for config_set tool."""

import pytest
from unittest.mock import MagicMock

from ploston_core.config import StagedConfig
from ploston_core.config.tools.config_set import (
    handle_config_set,
    detect_plaintext_secrets,
)


class TestDetectPlaintextSecrets:
    """Tests for detect_plaintext_secrets helper."""

    def test_no_warning_for_env_var(self):
        """No warning when using env var reference."""
        warnings = detect_plaintext_secrets("api_key", "${API_KEY}")
        assert len(warnings) == 0

    def test_warning_for_plaintext_password(self):
        """Warning for plaintext password."""
        warnings = detect_plaintext_secrets("database.password", "secret123")
        assert len(warnings) == 1
        assert "plaintext password" in warnings[0]["warning"]

    def test_warning_for_plaintext_api_key(self):
        """Warning for plaintext API key."""
        warnings = detect_plaintext_secrets("github.api_key", "ghp_xxx")
        assert len(warnings) == 1
        assert "API key" in warnings[0]["warning"]

    def test_warning_for_plaintext_token(self):
        """Warning for plaintext token."""
        warnings = detect_plaintext_secrets("auth.token", "abc123")
        assert len(warnings) == 1
        assert "token" in warnings[0]["warning"]

    def test_no_warning_for_non_secret_path(self):
        """No warning for non-secret paths."""
        warnings = detect_plaintext_secrets("logging.level", "DEBUG")
        assert len(warnings) == 0


class TestHandleConfigSet:
    """Tests for handle_config_set."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        # Return a mock ValidationResult
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = []
        staged.validate.return_value = mock_result
        return staged

    @pytest.mark.asyncio
    async def test_set_value(self, mock_staged_config):
        """Set a config value."""
        result = await handle_config_set(
            {"path": "logging.level", "value": "DEBUG"},
            mock_staged_config,
        )

        assert result["staged"] is True
        assert result["path"] == "logging.level"
        mock_staged_config.set.assert_called_once_with("logging.level", "DEBUG")

    @pytest.mark.asyncio
    async def test_set_nested_object(self, mock_staged_config):
        """Set a nested object value."""
        server_config = {"command": "npx", "args": ["@github/mcp"]}
        result = await handle_config_set(
            {"path": "mcp.servers.github", "value": server_config},
            mock_staged_config,
        )

        assert result["staged"] is True
        mock_staged_config.set.assert_called_once_with("mcp.servers.github", server_config)

    @pytest.mark.asyncio
    async def test_set_without_path_fails(self, mock_staged_config):
        """Set without path returns error."""
        result = await handle_config_set(
            {"value": "DEBUG"},
            mock_staged_config,
        )

        assert result["staged"] is False
        assert "path is required" in result["error"]

    @pytest.mark.asyncio
    async def test_set_returns_validation_errors(self, mock_staged_config):
        """Set returns validation errors but still stages."""
        mock_error = MagicMock()
        mock_error.path = "logging.level"
        mock_error.message = "Invalid value"

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_error]
        mock_result.warnings = []
        mock_staged_config.validate.return_value = mock_result

        result = await handle_config_set(
            {"path": "logging.level", "value": "INVALID"},
            mock_staged_config,
        )

        assert result["staged"] is True  # Still staged
        assert result["validation"]["valid"] is False
        assert len(result["validation"]["errors"]) == 1

    @pytest.mark.asyncio
    async def test_set_warns_on_plaintext_secret(self, mock_staged_config):
        """Set warns on plaintext secret."""
        result = await handle_config_set(
            {"path": "database.password", "value": "secret123"},
            mock_staged_config,
        )

        assert result["staged"] is True
        assert len(result["validation"]["warnings"]) == 1
        assert "plaintext password" in result["validation"]["warnings"][0]["warning"]
