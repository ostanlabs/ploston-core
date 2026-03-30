"""Unit tests for ToolInvoker runner dispatch path.

Tests U-06 through U-12 from UNIFIED_TOOL_RESOLVER_SPEC.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.errors import AELError, create_error
from ploston_core.invoker import ToolCallResult, ToolInvoker
from ploston_core.types import ToolSource, ToolStatus


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    # For CP path: create a tool definition
    tool = MagicMock()
    tool.status = ToolStatus.AVAILABLE
    registry.get_or_raise.return_value = tool
    router = MagicMock()
    router.source = ToolSource.MCP
    router.server_name = "slack"
    registry.get_router.return_value = router
    return registry


@pytest.fixture
def mock_mcp_manager():
    return MagicMock()


@pytest.fixture
def mock_sandbox_factory():
    return MagicMock()


@pytest.fixture
def mock_dispatcher():
    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value={"output": "success"})
    return dispatcher


@pytest.fixture
def invoker(mock_registry, mock_mcp_manager, mock_sandbox_factory, mock_dispatcher):
    return ToolInvoker(
        tool_registry=mock_registry,
        mcp_manager=mock_mcp_manager,
        sandbox_factory=mock_sandbox_factory,
        runner_dispatcher=mock_dispatcher,
    )


@pytest.fixture
def invoker_no_dispatcher(mock_registry, mock_mcp_manager, mock_sandbox_factory):
    return ToolInvoker(
        tool_registry=mock_registry,
        mcp_manager=mock_mcp_manager,
        sandbox_factory=mock_sandbox_factory,
        runner_dispatcher=None,
    )


class TestInvokerRunnerPath:
    """U-06 through U-12: ToolInvoker runner dispatch path."""

    @pytest.mark.asyncio
    async def test_u06_runner_prefixed_routes_to_dispatcher(
        self, invoker, mock_dispatcher, mock_registry
    ):
        """U-06: invoke() with runner-prefixed name routes to RunnerDispatcher, not ToolRegistry."""
        result = await invoker.invoke(
            "macbook-pro-local__github__actions_list",
            {"repo": "test"},
        )
        mock_dispatcher.dispatch.assert_called_once()
        # ToolRegistry should NOT be queried for runner tools
        mock_registry.get_or_raise.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_u07_strips_runner_prefix(self, invoker, mock_dispatcher):
        """U-07: invoke() strips runner prefix correctly before dispatching."""
        await invoker.invoke(
            "macbook-pro-local__github__actions_list",
            {"repo": "test"},
        )
        call_kwargs = mock_dispatcher.dispatch.call_args.kwargs
        assert call_kwargs["runner_name"] == "macbook-pro-local"
        assert call_kwargs["tool_name"] == "github__actions_list"

    @pytest.mark.asyncio
    async def test_u08_no_prefix_continues_cp_path(self, invoker, mock_registry, mock_mcp_manager):
        """U-08: invoke() with no runner prefix continues to CP path as before."""
        mcp_result = MagicMock()
        mcp_result.is_error = False
        mcp_result.content = "ok"
        mcp_result.structured_content = None
        mock_mcp_manager.call_tool = AsyncMock(return_value=mcp_result)

        result = await invoker.invoke("slack__post_message", {"text": "hi"})
        mock_registry.get_or_raise.assert_called_once_with("slack__post_message")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_u09_no_dispatcher_raises(self, invoker_no_dispatcher):
        """U-09: invoke() with runner prefix + no dispatcher raises TOOL_UNAVAILABLE."""
        with pytest.raises(AELError) as exc_info:
            await invoker_no_dispatcher.invoke("macbook-pro-local__github__actions_list", {})
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u10_wraps_success_in_result(self, invoker, mock_dispatcher):
        """U-10: invoke() wraps dispatcher output in ToolCallResult(success=True)."""
        mock_dispatcher.dispatch.return_value = {"items": [1, 2, 3]}
        result = await invoker.invoke("macbook-pro-local__github__actions_list", {})
        assert isinstance(result, ToolCallResult)
        assert result.success is True
        assert result.output == {"items": [1, 2, 3]}
        assert result.tool_name == "macbook-pro-local__github__actions_list"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_u11_wraps_exception_in_result(self, invoker, mock_dispatcher):
        """U-11: invoke() wraps dispatcher exception in ToolCallResult(success=False)."""
        mock_dispatcher.dispatch.side_effect = create_error(
            "TOOL_UNAVAILABLE", tool_name="test", reason="Runner offline"
        )
        result = await invoker.invoke("macbook-pro-local__github__actions_list", {})
        assert isinstance(result, ToolCallResult)
        assert result.success is False
        assert result.error is not None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_u12_multi_segment_runner_name(self, invoker, mock_dispatcher):
        """U-12: invoke() correctly parses multi-segment canonical names."""
        await invoker.invoke(
            "my-runner__my-mcp__my_tool",
            {"arg": "val"},
        )
        call_kwargs = mock_dispatcher.dispatch.call_args.kwargs
        assert call_kwargs["runner_name"] == "my-runner"
        assert call_kwargs["tool_name"] == "my-mcp__my_tool"


class TestInvokerPrefixDetection:
    """U-20 through U-24: Prefix detection edge cases."""

    @pytest.mark.asyncio
    async def test_u20_single_double_underscore_is_cp(
        self, invoker, mock_registry, mock_mcp_manager
    ):
        """U-20: name with single __ (no runner prefix) uses CP path."""
        mcp_result = MagicMock()
        mcp_result.is_error = False
        mcp_result.content = "ok"
        mcp_result.structured_content = None
        mock_mcp_manager.call_tool = AsyncMock(return_value=mcp_result)

        await invoker.invoke("slack__post_message", {"text": "hi"})
        mock_registry.get_or_raise.assert_called_once()

    @pytest.mark.asyncio
    async def test_u21_bare_name_is_cp(self, invoker, mock_registry, mock_mcp_manager):
        """U-21: bare name (no __) uses CP path."""
        mcp_result = MagicMock()
        mcp_result.is_error = False
        mcp_result.content = "ok"
        mcp_result.structured_content = None
        mock_mcp_manager.call_tool = AsyncMock(return_value=mcp_result)

        await invoker.invoke("python_exec", {"code": "1+1"})
        mock_registry.get_or_raise.assert_called_once()

    @pytest.mark.asyncio
    async def test_u22_triple_segment_is_runner(self, invoker, mock_dispatcher):
        """U-22: name with two __ separators (3 segments) uses runner path."""
        await invoker.invoke("runner__mcp__tool", {})
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_u23_timeout_passthrough(self, invoker, mock_dispatcher):
        """U-23: timeout_seconds is forwarded to dispatcher."""
        await invoker.invoke(
            "runner__mcp__tool",
            {},
            timeout_seconds=300,
        )
        call_kwargs = mock_dispatcher.dispatch.call_args.kwargs
        assert call_kwargs["timeout"] == 300

    @pytest.mark.asyncio
    async def test_u24_default_timeout(self, invoker, mock_dispatcher):
        """U-24: default timeout of 60.0 when timeout_seconds is None."""
        await invoker.invoke("runner__mcp__tool", {})
        call_kwargs = mock_dispatcher.dispatch.call_args.kwargs
        assert call_kwargs["timeout"] == 60.0
