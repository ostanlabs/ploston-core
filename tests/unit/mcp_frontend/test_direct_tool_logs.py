"""Tests for direct tool call structured logging (T-705 Part 2).

Verifies that _execute_tool and _execute_runner_tool emit
direct_tool_called, direct_tool_completed, and direct_tool_failed events
with source="tool".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.config import Mode, ModeManager
from ploston_core.errors import AELError
from ploston_core.errors.errors import ErrorCategory
from ploston_core.invoker.types import ToolCallResult
from ploston_core.mcp_frontend.server import MCPFrontend, _split_tool_name
from ploston_core.types import LogLevel


@pytest.fixture
def mock_logger():
    """Create a mock AELLogger that captures _log calls."""
    logger = MagicMock()
    logger._log = MagicMock()
    logger.config = MagicMock()
    logger.config.components = {"direct": True}
    return logger


@pytest.fixture
def frontend(mock_logger):
    """Create MCPFrontend with mock dependencies."""
    engine = MagicMock()
    tool_registry = MagicMock()
    workflow_registry = MagicMock()
    tool_invoker = MagicMock()
    mode_manager = ModeManager(initial_mode=Mode.RUNNING)

    return MCPFrontend(
        workflow_engine=engine,
        tool_registry=tool_registry,
        workflow_registry=workflow_registry,
        tool_invoker=tool_invoker,
        logger=mock_logger,
        mode_manager=mode_manager,
    )


class TestExecuteToolLogs:
    """Tests for _execute_tool structured log events."""

    @pytest.mark.asyncio
    async def test_emits_direct_tool_called(self, frontend, mock_logger):
        """_execute_tool emits direct_tool_called with source='tool'."""
        frontend._tool_invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=True, output="ok", duration_ms=10, tool_name="test_tool"
            )
        )
        await frontend._execute_tool("test_tool", {})

        # First _log call should be the START event
        first_call = mock_logger._log.call_args_list[0]
        assert first_call[0][0] == LogLevel.INFO
        context = first_call[0][3]
        assert context["source"] == "tool"
        assert context["event"] == "direct_tool_called"
        assert context["tool_name"] == "test_tool"
        assert context["bridge"] == ""

    @pytest.mark.asyncio
    async def test_emits_direct_tool_completed_on_success(self, frontend, mock_logger):
        """_execute_tool on success emits direct_tool_completed with duration_ms."""
        frontend._tool_invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=True, output={"data": "result"}, duration_ms=42, tool_name="test_tool"
            )
        )
        await frontend._execute_tool("test_tool", {"key": "val"})

        # Second _log call should be the COMPLETED event
        second_call = mock_logger._log.call_args_list[1]
        assert second_call[0][0] == LogLevel.INFO
        context = second_call[0][3]
        assert context["source"] == "tool"
        assert context["event"] == "direct_tool_completed"
        assert context["tool_name"] == "test_tool"
        assert "duration_ms" in context

    @pytest.mark.asyncio
    async def test_emits_direct_tool_failed_on_error(self, frontend, mock_logger):
        """_execute_tool on failure emits direct_tool_failed with error details."""
        error = AELError(
            code="TOOL_FAILED",
            category=ErrorCategory.TOOL,
            message="Connection refused",
        )
        frontend._tool_invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=False, output=None, duration_ms=5, tool_name="test_tool", error=error
            )
        )
        await frontend._execute_tool("test_tool", {})

        # Second _log call should be the FAILED event
        second_call = mock_logger._log.call_args_list[1]
        assert second_call[0][0] == LogLevel.ERROR
        context = second_call[0][3]
        assert context["source"] == "tool"
        assert context["event"] == "direct_tool_failed"
        assert context["tool_name"] == "test_tool"
        assert context["error"] == "Connection refused"
        assert context["error_type"] == "TOOL_FAILED"
        assert "duration_ms" in context

    @pytest.mark.asyncio
    async def test_no_source_workflow_in_direct_tool_events(self, frontend, mock_logger):
        """Direct tool events must never have source='workflow'."""
        frontend._tool_invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=True, output="ok", duration_ms=10, tool_name="test_tool"
            )
        )
        await frontend._execute_tool("test_tool", {})

        for log_call in mock_logger._log.call_args_list:
            context = log_call[0][3]
            assert context["source"] != "workflow"


class TestSplitToolName:
    """Tests for _split_tool_name helper."""

    def test_qualified_name(self):
        bridge, tool = _split_tool_name("obsidian-mcp__read_docs")
        assert bridge == "obsidian-mcp"
        assert tool == "read_docs"

    def test_simple_name(self):
        bridge, tool = _split_tool_name("test_tool")
        assert bridge == ""
        assert tool == "test_tool"

    def test_multiple_double_underscores(self):
        """Only the first __ is used as separator."""
        bridge, tool = _split_tool_name("my-server__some__nested__tool")
        assert bridge == "my-server"
        assert tool == "some__nested__tool"


class TestExecuteToolBridgeSplit:
    """Tests that qualified tool names are split into bridge + tool_name."""

    @pytest.mark.asyncio
    async def test_qualified_name_splits_bridge_and_tool(self, frontend, mock_logger):
        """obsidian-mcp__read_docs → bridge='obsidian-mcp', tool_name='read_docs'."""
        frontend._tool_invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=True, output="ok", duration_ms=10, tool_name="obsidian-mcp__read_docs"
            )
        )
        await frontend._execute_tool("obsidian-mcp__read_docs", {})

        first_call = mock_logger._log.call_args_list[0]
        context = first_call[0][3]
        assert context["bridge"] == "obsidian-mcp"
        assert context["tool_name"] == "read_docs"

        second_call = mock_logger._log.call_args_list[1]
        context = second_call[0][3]
        assert context["bridge"] == "obsidian-mcp"
        assert context["tool_name"] == "read_docs"
