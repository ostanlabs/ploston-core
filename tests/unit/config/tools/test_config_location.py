"""Unit tests for config_location tool."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from ploston_core.config.tools.config_location import handle_config_location


class TestHandleConfigLocation:
    """Tests for handle_config_location."""

    @pytest.fixture
    def mock_config_loader(self):
        """Create mock config loader."""
        loader = MagicMock()
        loader._config_path = "/path/to/config.yaml"
        return loader

    @pytest.mark.asyncio
    async def test_get_current_location(self, mock_config_loader):
        """Get current location when no arguments."""
        result = await handle_config_location({}, None, mock_config_loader)

        assert result["current_source"] == "/path/to/config.yaml"
        assert "write_target" in result
        assert "available_scopes" in result

    @pytest.mark.asyncio
    async def test_set_project_scope(self, mock_config_loader, tmp_path, monkeypatch):
        """Set project scope."""
        monkeypatch.chdir(tmp_path)
        
        result = await handle_config_location(
            {"scope": "project"},
            None,
            mock_config_loader,
        )

        assert result["write_target"] == "./ael-config.yaml"
        assert "new_location" in result

    @pytest.mark.asyncio
    async def test_set_user_scope(self, mock_config_loader, tmp_path, monkeypatch):
        """Set user scope."""
        result = await handle_config_location(
            {"scope": "user"},
            None,
            mock_config_loader,
        )

        assert ".ael" in result["write_target"]
        assert "config.yaml" in result["write_target"]

    @pytest.mark.asyncio
    async def test_set_custom_path(self, mock_config_loader, tmp_path):
        """Set custom path."""
        custom_path = str(tmp_path / "custom-config.yaml")
        
        result = await handle_config_location(
            {"path": custom_path},
            None,
            mock_config_loader,
        )

        assert result["write_target"] == custom_path
        assert result["new_location"] == custom_path

    @pytest.mark.asyncio
    async def test_invalid_scope(self, mock_config_loader):
        """Invalid scope returns error."""
        result = await handle_config_location(
            {"scope": "invalid"},
            None,
            mock_config_loader,
        )

        assert "error" in result
        assert "Invalid scope" in result["error"]

    @pytest.mark.asyncio
    async def test_current_location_preserved(self, mock_config_loader):
        """Current location is shown when set."""
        result = await handle_config_location(
            {},
            "/custom/location.yaml",
            mock_config_loader,
        )

        assert result["write_target"] == "/custom/location.yaml"
