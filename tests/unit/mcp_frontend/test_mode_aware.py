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


class TestRunnerToolRouting:
    """Tests for runner tool routing (DEC-123).

    Tests prefix-based routing where tools are namespaced as runner-name:tool.
    """

    @pytest.fixture
    def mock_runner_registry(self):
        """Create mock runner registry with connected runner."""
        from datetime import UTC, datetime

        from ploston_core.runner_management.registry import Runner, RunnerStatus

        registry = MagicMock()
        runner = Runner(
            id="runner-123",
            name="mac",
            token_hash="hash",
            status=RunnerStatus.CONNECTED,
            created_at=datetime.now(UTC),
        )
        # Runner stores tools as mcp__tool format
        runner.available_tools = [
            {"name": "fs__read_file", "description": "Read file", "inputSchema": {}},
            {"name": "fs__write_file", "description": "Write file", "inputSchema": {}},
        ]
        registry.get_by_name.return_value = runner
        registry.get.return_value = runner
        registry.list.return_value = [runner]  # For tools/list
        return registry

    @pytest.fixture
    def mock_runner_registry_disconnected(self):
        """Create mock runner registry with disconnected runner."""
        from datetime import UTC, datetime

        from ploston_core.runner_management.registry import Runner, RunnerStatus

        registry = MagicMock()
        runner = Runner(
            id="runner-123",
            name="mac",
            token_hash="hash",
            status=RunnerStatus.DISCONNECTED,
            created_at=datetime.now(UTC),
        )
        registry.get_by_name.return_value = runner
        registry.list.return_value = [runner]  # For tools/list
        return registry

    @pytest.fixture
    def frontend_with_runner(
        self,
        mock_runner_registry,
    ):
        """Create frontend with runner registry in running mode."""
        mode_manager = ModeManager(initial_mode=Mode.RUNNING)
        return MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            mode_manager=mode_manager,
            runner_registry=mock_runner_registry,
        )

    @pytest.fixture
    def frontend_with_disconnected_runner(
        self,
        mock_runner_registry_disconnected,
    ):
        """Create frontend with disconnected runner."""
        mode_manager = ModeManager(initial_mode=Mode.RUNNING)
        return MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            mode_manager=mode_manager,
            runner_registry=mock_runner_registry_disconnected,
        )

    async def test_tools_list_includes_runner_tools(self, frontend_with_runner):
        """Test that tools/list includes runner tools with prefix."""
        result = await frontend_with_runner._handle_tools_list({})

        tools = result["tools"]
        tool_names = [t["name"] for t in tools]

        # Runner tools should be prefixed with runner__mcp__tool format
        assert "mac__fs__read_file" in tool_names
        assert "mac__fs__write_file" in tool_names

    async def test_runner_tool_has_correct_schema(self, frontend_with_runner):
        """Test that runner tools have correct schema in tools/list."""
        result = await frontend_with_runner._handle_tools_list({})

        tools = result["tools"]
        runner_tool = next((t for t in tools if t["name"] == "mac__fs__read_file"), None)

        assert runner_tool is not None
        assert runner_tool["description"] == "Read file"
        assert "inputSchema" in runner_tool

    async def test_tool_call_parses_runner_prefix(self, frontend_with_runner):
        """Test that tool call correctly parses runner prefix."""
        with (
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
            ) as mock_send,
            patch(
                "ploston_core.mcp_frontend.server.is_runner_connected",
                return_value=True,
            ),
        ):
            mock_send.return_value = {"content": [{"type": "text", "text": "ok"}]}

            await frontend_with_runner._handle_tools_call(
                {"name": "mac__fs__read_file", "arguments": {"path": "/tmp/test"}}
            )

            # Verify the tool was routed to the runner
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args.kwargs["runner_id"] == "runner-123"
            # Tool name passed to runner is mcp__tool format
            assert call_args.kwargs["tool_name"] == "fs__read_file"
            assert call_args.kwargs["arguments"] == {"path": "/tmp/test"}

    async def test_tool_call_to_disconnected_runner_fails(self, frontend_with_disconnected_runner):
        """Test that tool call to disconnected runner fails."""
        with pytest.raises(AELError) as exc_info:
            await frontend_with_disconnected_runner._handle_tools_call(
                {"name": "mac__fs__read_file", "arguments": {}}
            )

        assert "not connected" in exc_info.value.message

    async def test_tool_call_to_unknown_runner_fails(self, frontend_with_runner):
        """Test that tool call to unknown runner fails."""
        frontend_with_runner._runner_registry.get_by_name.return_value = None

        with pytest.raises(AELError) as exc_info:
            await frontend_with_runner._handle_tools_call(
                {"name": "unknown__fs__read_file", "arguments": {}}
            )

        assert "not found" in exc_info.value.message

    async def test_tool_call_without_runner_registry_fails(self):
        """Test that runner tool call without registry fails."""
        mode_manager = ModeManager(initial_mode=Mode.RUNNING)
        frontend = MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            mode_manager=mode_manager,
            runner_registry=None,  # No runner registry
        )

        with pytest.raises(AELError) as exc_info:
            await frontend._handle_tools_call({"name": "mac__fs__read_file", "arguments": {}})

        assert "not configured" in exc_info.value.message

    async def test_unprefixed_tool_not_routed_to_runner(self, frontend_with_runner):
        """Test that unprefixed tools are not routed to runner."""
        # Mock the tool invoker for CP tools
        frontend_with_runner._tool_invoker.invoke = AsyncMock(
            return_value=MagicMock(success=True, output="result")
        )

        with patch(
            "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
            new_callable=AsyncMock,
        ) as mock_send:
            await frontend_with_runner._handle_tools_call(
                {"name": "slack_post", "arguments": {"message": "hello"}}
            )

            # Runner routing should NOT be called
            mock_send.assert_not_called()

    async def test_runner_tool_result_formatting(self, frontend_with_runner):
        """Test that runner tool results are formatted correctly."""
        with (
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
            ) as mock_send,
            patch(
                "ploston_core.mcp_frontend.server.is_runner_connected",
                return_value=True,
            ),
        ):
            # Test MCP format result
            mock_send.return_value = {
                "content": [{"type": "text", "text": "file contents"}],
                "isError": False,
            }

            result = await frontend_with_runner._handle_tools_call(
                {"name": "mac__fs__read_file", "arguments": {"path": "/tmp/test"}}
            )

            assert result["content"][0]["text"] == "file contents"
            assert result["isError"] is False

    async def test_runner_tool_error_formatting(self, frontend_with_runner):
        """Test that runner tool errors are formatted correctly."""
        with (
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
            ) as mock_send,
            patch(
                "ploston_core.mcp_frontend.server.is_runner_connected",
                return_value=True,
            ),
        ):
            # Test error result
            mock_send.return_value = {"error": "File not found"}

            result = await frontend_with_runner._handle_tools_call(
                {"name": "mac__fs__read_file", "arguments": {"path": "/nonexistent"}}
            )

            assert result["isError"] is True
            assert "File not found" in result["content"][0]["text"]
