"""Unit tests for configure tool."""

from unittest.mock import MagicMock

import pytest

from ploston_core.config import Mode
from ploston_core.config.tools.configure import handle_configure


class TestHandleConfigure:
    """Tests for handle_configure."""

    @pytest.fixture
    def mock_mode_manager(self):
        """Create mock mode manager."""
        manager = MagicMock()
        manager.running_workflow_count = 0
        manager.set_mode = MagicMock()
        return manager

    @pytest.mark.asyncio
    async def test_configure_switches_mode(self, mock_mode_manager):
        """Configure switches to configuration mode."""
        result = await handle_configure({}, mock_mode_manager)

        assert result["success"] is True
        assert result["mode"] == "configuration"
        mock_mode_manager.set_mode.assert_called_once_with(Mode.CONFIGURATION)

    @pytest.mark.asyncio
    async def test_configure_reports_running_workflows(self, mock_mode_manager):
        """Configure reports running workflow count."""
        mock_mode_manager.running_workflow_count = 3

        result = await handle_configure({}, mock_mode_manager)

        assert result["running_workflows"] == 3
        assert "3 workflow(s) still running" in result["message"]

    @pytest.mark.asyncio
    async def test_configure_no_running_workflows(self, mock_mode_manager):
        """Configure message when no running workflows."""
        mock_mode_manager.running_workflow_count = 0

        result = await handle_configure({}, mock_mode_manager)

        assert result["running_workflows"] == 0
        assert "Switched to configuration mode." == result["message"]

    @pytest.mark.asyncio
    async def test_configure_without_mode_manager(self):
        """Configure fails without mode manager."""
        result = await handle_configure({}, None)

        assert result["success"] is False
        assert "Mode manager not available" in result["error"]
