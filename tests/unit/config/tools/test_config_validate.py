"""Unit tests for config_validate tool."""

import pytest
from unittest.mock import MagicMock

from ploston_core.config.tools.config_validate import handle_config_validate


class TestHandleConfigValidate:
    """Tests for handle_config_validate."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        staged.changes = {}
        return staged

    @pytest.mark.asyncio
    async def test_validate_valid_config(self, mock_staged_config):
        """Validate returns valid for good config."""
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = []
        mock_staged_config.validate.return_value = mock_result

        result = await handle_config_validate({}, mock_staged_config)

        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) == 0

    @pytest.mark.asyncio
    async def test_validate_invalid_config(self, mock_staged_config):
        """Validate returns errors for invalid config."""
        mock_error = MagicMock()
        mock_error.path = "mcp.servers.github"
        mock_error.message = "Missing command"

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_error]
        mock_result.warnings = []
        mock_staged_config.validate.return_value = mock_result

        result = await handle_config_validate({}, mock_staged_config)

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["path"] == "mcp.servers.github"

    @pytest.mark.asyncio
    async def test_validate_with_warnings(self, mock_staged_config):
        """Validate returns warnings."""
        mock_warning = MagicMock()
        mock_warning.path = "logging.level"
        mock_warning.message = "DEBUG is verbose"

        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = [mock_warning]
        mock_staged_config.validate.return_value = mock_result

        result = await handle_config_validate({}, mock_staged_config)

        assert result["valid"] is True
        assert len(result["warnings"]) == 1

    @pytest.mark.asyncio
    async def test_validate_reports_staged_changes_count(self, mock_staged_config):
        """Validate reports number of staged changes."""
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = []
        mock_staged_config.validate.return_value = mock_result
        mock_staged_config.changes = {
            "logging.level": "DEBUG",
            "mcp.servers.github": {"command": "npx"},
        }

        result = await handle_config_validate({}, mock_staged_config)

        assert result["staged_changes_count"] == 2
