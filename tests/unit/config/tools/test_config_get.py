"""Unit tests for config_get tool."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from ploston_core.config import ConfigLoader, StagedConfig, AELConfig
from ploston_core.config.tools.config_get import (
    handle_config_get,
    get_nested_value,
    config_to_dict,
)


class TestGetNestedValue:
    """Tests for get_nested_value helper."""

    def test_empty_path_returns_object(self):
        """Empty path returns the whole object."""
        obj = {"a": 1, "b": 2}
        assert get_nested_value(obj, "") == obj

    def test_single_level_dict(self):
        """Single level path in dict."""
        obj = {"a": 1, "b": 2}
        assert get_nested_value(obj, "a") == 1

    def test_nested_dict(self):
        """Nested path in dict."""
        obj = {"a": {"b": {"c": 3}}}
        assert get_nested_value(obj, "a.b.c") == 3

    def test_missing_key_returns_none(self):
        """Missing key returns None."""
        obj = {"a": 1}
        assert get_nested_value(obj, "b") is None

    def test_missing_nested_key_returns_none(self):
        """Missing nested key returns None."""
        obj = {"a": {"b": 1}}
        assert get_nested_value(obj, "a.c") is None


class TestConfigToDict:
    """Tests for config_to_dict helper."""

    def test_none_returns_none(self):
        """None returns None."""
        assert config_to_dict(None) is None

    def test_dict_returns_dict(self):
        """Dict returns dict."""
        obj = {"a": 1}
        assert config_to_dict(obj) == {"a": 1}

    def test_nested_dict(self):
        """Nested dict is preserved."""
        obj = {"a": {"b": 1}}
        assert config_to_dict(obj) == {"a": {"b": 1}}

    def test_list_is_preserved(self):
        """List is preserved."""
        obj = [1, 2, 3]
        assert config_to_dict(obj) == [1, 2, 3]


class TestHandleConfigGet:
    """Tests for handle_config_get."""

    @pytest.fixture
    def mock_config_loader(self):
        """Create mock config loader."""
        loader = MagicMock()
        loader._config_path = "/path/to/config.yaml"
        return loader

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        staged.has_changes.return_value = False
        staged.get_merged.return_value = {
            "mcp": {"servers": {"github": {"command": "npx"}}},
            "logging": {"level": "INFO"},
        }
        return staged

    @pytest.mark.asyncio
    async def test_get_entire_config(self, mock_staged_config, mock_config_loader):
        """Get entire config when no path specified."""
        result = await handle_config_get({}, mock_staged_config, mock_config_loader)

        assert result["path"] == "(root)"
        assert result["value"]["mcp"]["servers"]["github"]["command"] == "npx"
        assert result["source"] == "/path/to/config.yaml"
        assert result["has_staged_changes"] is False

    @pytest.mark.asyncio
    async def test_get_nested_path(self, mock_staged_config, mock_config_loader):
        """Get specific nested path."""
        result = await handle_config_get(
            {"path": "mcp.servers.github"},
            mock_staged_config,
            mock_config_loader,
        )

        assert result["path"] == "mcp.servers.github"
        assert result["value"]["command"] == "npx"

    @pytest.mark.asyncio
    async def test_get_with_staged_changes(self, mock_staged_config, mock_config_loader):
        """Report staged changes flag."""
        mock_staged_config.has_changes.return_value = True

        result = await handle_config_get({}, mock_staged_config, mock_config_loader)

        assert result["has_staged_changes"] is True

    @pytest.mark.asyncio
    async def test_get_missing_path(self, mock_staged_config, mock_config_loader):
        """Get missing path returns None value."""
        result = await handle_config_get(
            {"path": "nonexistent.path"},
            mock_staged_config,
            mock_config_loader,
        )

        assert result["path"] == "nonexistent.path"
        assert result["value"] is None
