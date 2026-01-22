"""Unit tests for config_done tool."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from ploston_core.config import Mode
from ploston_core.config.tools.config_done import handle_config_done


class TestHandleConfigDone:
    """Tests for handle_config_done."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = []
        staged.validate.return_value = mock_result
        staged.get_merged.return_value = {}  # No MCP config
        staged.set_target_path = MagicMock()
        staged.write = MagicMock()
        staged.clear = MagicMock()
        return staged

    @pytest.fixture
    def mock_config_loader(self):
        """Create mock config loader."""
        return MagicMock()

    @pytest.fixture
    def mock_mode_manager(self):
        """Create mock mode manager."""
        manager = MagicMock()
        manager.set_mode = MagicMock()
        return manager

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create mock MCP manager."""
        manager = MagicMock()
        manager.connect = AsyncMock()
        manager.list_tools = AsyncMock(return_value=[{"name": "tool1"}])
        return manager

    @pytest.mark.asyncio
    async def test_config_done_success_no_mcp(
        self, mock_staged_config, mock_config_loader, mock_mode_manager, tmp_path
    ):
        """Config done succeeds with no MCP servers."""
        write_path = str(tmp_path / "config.yaml")

        result = await handle_config_done(
            {},
            mock_staged_config,
            mock_config_loader,
            mock_mode_manager,
            None,  # No MCP manager
            write_path,
        )

        assert result["success"] is True
        assert result["mode"] == "running"
        assert result["config_written_to"] == write_path
        mock_mode_manager.set_mode.assert_called_once_with(Mode.RUNNING)
        mock_staged_config.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_done_validation_failure(
        self, mock_staged_config, mock_config_loader, mock_mode_manager
    ):
        """Config done fails on validation error."""
        mock_error = MagicMock()
        mock_error.path = "mcp.servers"
        mock_error.message = "Invalid config"

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_error]
        mock_staged_config.validate.return_value = mock_result

        result = await handle_config_done(
            {},
            mock_staged_config,
            mock_config_loader,
            mock_mode_manager,
            None,
            None,
        )

        assert result["success"] is False
        assert result["mode"] == "configuration"
        assert len(result["errors"]) == 1
        mock_mode_manager.set_mode.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_done_mcp_connection_failure(
        self, mock_staged_config, mock_config_loader, mock_mode_manager, mock_mcp_manager
    ):
        """Config done fails on MCP connection error."""
        # Setup config with MCP server (as dict, not dataclass)
        mock_staged_config.get_merged.return_value = {
            "mcp": {"servers": {"github": {"command": "npx"}}}
        }

        # Make connection fail
        mock_mcp_manager.connect = AsyncMock(side_effect=Exception("Connection failed"))

        result = await handle_config_done(
            {},
            mock_staged_config,
            mock_config_loader,
            mock_mode_manager,
            mock_mcp_manager,
            None,
        )

        assert result["success"] is False
        assert result["mode"] == "configuration"
        assert len(result["errors"]) == 1
        assert "Connection failed" in result["errors"][0]["error"]
        mock_mode_manager.set_mode.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_done_mcp_success(
        self, mock_staged_config, mock_config_loader, mock_mode_manager, mock_mcp_manager, tmp_path
    ):
        """Config done succeeds with MCP servers."""
        # Setup config with MCP server (as dict, not dataclass)
        mock_staged_config.get_merged.return_value = {
            "mcp": {"servers": {"github": {"command": "npx"}}}
        }

        write_path = str(tmp_path / "config.yaml")

        result = await handle_config_done(
            {},
            mock_staged_config,
            mock_config_loader,
            mock_mode_manager,
            mock_mcp_manager,
            write_path,
        )

        assert result["success"] is True
        assert result["mode"] == "running"
        assert result["capabilities"]["mcp_servers"]["github"]["status"] == "connected"
        assert result["capabilities"]["total_tools"] == 1

    @pytest.mark.asyncio
    async def test_config_done_write_failure(
        self, mock_staged_config, mock_config_loader, mock_mode_manager
    ):
        """Config done fails on write error."""
        mock_staged_config.write.side_effect = Exception("Permission denied")

        result = await handle_config_done(
            {},
            mock_staged_config,
            mock_config_loader,
            mock_mode_manager,
            None,
            "/readonly/path.yaml",
        )

        assert result["success"] is False
        assert result["mode"] == "configuration"
        assert "Permission denied" in result["errors"][0]["error"]
