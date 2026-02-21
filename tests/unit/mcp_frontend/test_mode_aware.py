"""Unit tests for mode-aware MCPFrontend."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ploston_core.config import Mode, ModeManager
from ploston_core.errors import AELError
from ploston_core.mcp_frontend.server import MCPFrontend


class TestModeAwareMCPFrontend:
    """Tests for mode-aware MCPFrontend behavior."""

    @pytest.fixture
    def mock_workflow_engine(self):
        """Create mock workflow engine."""
        return MagicMock()

    @pytest.fixture
    def mock_tool_registry(self):
        """Create mock tool registry."""
        registry = MagicMock()
        registry.get_for_mcp_exposure.return_value = [
            {"name": "tool1", "description": "Test tool 1"},
            {"name": "tool2", "description": "Test tool 2"},
        ]
        return registry

    @pytest.fixture
    def mock_workflow_registry(self):
        """Create mock workflow registry."""
        registry = MagicMock()
        registry.get_for_mcp_exposure.return_value = [
            {"name": "workflow_test", "description": "Test workflow"},
        ]
        return registry

    @pytest.fixture
    def mock_tool_invoker(self):
        """Create mock tool invoker."""
        return MagicMock()

    @pytest.fixture
    def mock_config_tool_registry(self):
        """Create mock config tool registry."""
        registry = MagicMock()
        registry.get_for_mcp_exposure.return_value = [
            {"name": "ael:config_get", "description": "Get config"},
            {"name": "ael:config_set", "description": "Set config"},
        ]
        registry.get_configure_tool_for_mcp_exposure.return_value = {
            "name": "configure",
            "description": "Switch to config mode",
        }
        registry.call = AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]})
        return registry

    @pytest.fixture
    def mode_manager_config(self):
        """Create mode manager in configuration mode."""
        return ModeManager(initial_mode=Mode.CONFIGURATION)

    @pytest.fixture
    def mode_manager_running(self):
        """Create mode manager in running mode."""
        return ModeManager(initial_mode=Mode.RUNNING)

    @pytest.fixture
    def frontend_config_mode(
        self,
        mock_workflow_engine,
        mock_tool_registry,
        mock_workflow_registry,
        mock_tool_invoker,
        mock_config_tool_registry,
        mode_manager_config,
    ):
        """Create frontend in configuration mode."""
        return MCPFrontend(
            workflow_engine=mock_workflow_engine,
            tool_registry=mock_tool_registry,
            workflow_registry=mock_workflow_registry,
            tool_invoker=mock_tool_invoker,
            mode_manager=mode_manager_config,
            config_tool_registry=mock_config_tool_registry,
        )

    @pytest.fixture
    def frontend_running_mode(
        self,
        mock_workflow_engine,
        mock_tool_registry,
        mock_workflow_registry,
        mock_tool_invoker,
        mock_config_tool_registry,
        mode_manager_running,
    ):
        """Create frontend in running mode."""
        return MCPFrontend(
            workflow_engine=mock_workflow_engine,
            tool_registry=mock_tool_registry,
            workflow_registry=mock_workflow_registry,
            tool_invoker=mock_tool_invoker,
            mode_manager=mode_manager_running,
            config_tool_registry=mock_config_tool_registry,
        )

    # Tools list tests

    async def test_tools_list_config_mode_returns_config_tools(self, frontend_config_mode):
        """In config mode, tools/list returns only config tools."""
        result = await frontend_config_mode._handle_tools_list({})

        tools = result["tools"]
        tool_names = [t["name"] for t in tools]

        assert "ael:config_get" in tool_names
        assert "ael:config_set" in tool_names
        assert "tool1" not in tool_names
        assert "workflow_test" not in tool_names

    async def test_tools_list_running_mode_returns_all_tools(self, frontend_running_mode):
        """In running mode, tools/list returns all tools + workflows + configure."""
        result = await frontend_running_mode._handle_tools_list({})

        tools = result["tools"]
        tool_names = [t["name"] for t in tools]

        assert "tool1" in tool_names
        assert "tool2" in tool_names
        assert "workflow_test" in tool_names
        assert "configure" in tool_names
        # Config tools should NOT be in running mode
        assert "ael:config_get" not in tool_names

    # Tools call tests - config mode

    async def test_tools_call_config_mode_allows_config_tools(
        self, frontend_config_mode, mock_config_tool_registry
    ):
        """In config mode, config tools can be called."""
        await frontend_config_mode._handle_tools_call(
            {"name": "ael:config_get", "arguments": {"path": "server.port"}}
        )

        mock_config_tool_registry.call.assert_called_once()

    async def test_tools_call_config_mode_blocks_configure(self, frontend_config_mode):
        """In config mode, configure is not available."""
        with pytest.raises(AELError) as exc_info:
            await frontend_config_mode._handle_tools_call({"name": "configure", "arguments": {}})

        assert "only available in running mode" in exc_info.value.message

    async def test_tools_call_config_mode_blocks_workflows(self, frontend_config_mode):
        """In config mode, workflows are blocked."""
        with pytest.raises(AELError) as exc_info:
            await frontend_config_mode._handle_tools_call(
                {"name": "workflow_test", "arguments": {}}
            )

        assert "not available in configuration mode" in exc_info.value.message

    async def test_tools_call_config_mode_blocks_regular_tools(self, frontend_config_mode):
        """In config mode, regular tools are blocked."""
        with pytest.raises(AELError) as exc_info:
            await frontend_config_mode._handle_tools_call({"name": "tool1", "arguments": {}})

        assert "not available in configuration mode" in exc_info.value.message

    # Tools call tests - running mode

    async def test_tools_call_running_mode_allows_configure(
        self, frontend_running_mode, mock_config_tool_registry
    ):
        """In running mode, configure can be called."""
        await frontend_running_mode._handle_tools_call({"name": "configure", "arguments": {}})

        mock_config_tool_registry.call.assert_called_once_with("configure", {})

    async def test_tools_call_running_mode_blocks_config_tools(self, frontend_running_mode):
        """In running mode, config tools (except configure) are blocked."""
        with pytest.raises(AELError) as exc_info:
            await frontend_running_mode._handle_tools_call(
                {"name": "ael:config_get", "arguments": {}}
            )

        assert "only available in configuration mode" in exc_info.value.message

    # Mode change notification tests

    async def test_mode_change_sends_notification(
        self,
        mock_workflow_engine,
        mock_tool_registry,
        mock_workflow_registry,
        mock_tool_invoker,
        mock_config_tool_registry,
    ):
        """Mode change triggers tools/list_changed notification."""
        mode_manager = ModeManager(initial_mode=Mode.CONFIGURATION)

        frontend = MCPFrontend(
            workflow_engine=mock_workflow_engine,
            tool_registry=mock_tool_registry,
            workflow_registry=mock_workflow_registry,
            tool_invoker=mock_tool_invoker,
            mode_manager=mode_manager,
            config_tool_registry=mock_config_tool_registry,
        )

        with patch.object(
            frontend, "_send_tools_changed_notification", new_callable=AsyncMock
        ) as mock_notify:
            # Change mode
            mode_manager.set_mode(Mode.RUNNING)

            # Give asyncio.create_task a chance to run
            import asyncio

            await asyncio.sleep(0)

            mock_notify.assert_called_once()

    async def test_notification_format(self, frontend_config_mode):
        """Test the notification message format."""
        with patch(
            "ploston_core.mcp_frontend.server.write_message", new_callable=AsyncMock
        ) as mock_write:
            await frontend_config_mode._send_tools_changed_notification()

            mock_write.assert_called_once()
            notification = mock_write.call_args[0][0]

            assert notification["jsonrpc"] == "2.0"
            assert notification["method"] == "notifications/tools/list_changed"
            assert "id" not in notification  # Notifications don't have id


class TestModeManagerRegistration:
    """Tests for ModeManager callback registration."""

    def test_frontend_registers_callback(self):
        """Frontend registers callback with mode manager."""
        mode_manager = ModeManager()

        # Before creating frontend
        assert len(mode_manager._on_change_callbacks) == 0

        _frontend = MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            mode_manager=mode_manager,
        )

        # After creating frontend (frontend registers callback)
        assert len(mode_manager._on_change_callbacks) == 1

    def test_default_mode_manager_created(self):
        """Frontend creates default mode manager if none provided."""
        frontend = MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
        )

        assert frontend._mode_manager is not None
        assert frontend._mode_manager.mode == Mode.CONFIGURATION  # Default
