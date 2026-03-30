"""Integration tests for unified tool resolver (I-01 through I-03).

Verifies the full path: ToolInvoker → RunnerDispatcher → RunnerRegistry
without a live WebSocket connection.

Usage:
    pytest tests/integration/test_tool_resolver.py -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.errors import AELError
from ploston_core.invoker import ToolCallResult, ToolInvoker
from ploston_core.registry.types import ToolDefinition, ToolRouter
from ploston_core.runner_management.dispatcher import RunnerToolDispatcher
from ploston_core.runner_management.registry import RunnerRegistry
from ploston_core.types import ToolSource, ToolStatus

pytestmark = [pytest.mark.integration]


@pytest.fixture
def runner_registry() -> RunnerRegistry:
    """Real RunnerRegistry with a connected runner that has github MCP tools."""
    reg = RunnerRegistry()
    runner, _ = reg.create("macbook-pro-local")
    reg.set_connected(runner.id)
    reg.update_available_tools(
        runner.id,
        ["github__actions_list", "github__list_commits", "filesystem__read_file"],
    )
    return reg


@pytest.fixture
def tool_registry_no_github() -> MagicMock:
    """ToolRegistry that does NOT have github registered (CP doesn't host it)."""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolDefinition(
            name="slack__post_message",
            description="Post to Slack",
            source=ToolSource.MCP,
            server_name="slack",
            status=ToolStatus.AVAILABLE,
        ),
    ]

    # For non-runner tools, return a router
    def get_or_raise(name):
        if name == "slack__post_message":
            return ToolDefinition(
                name="slack__post_message",
                description="Post to Slack",
                source=ToolSource.MCP,
                server_name="slack",
                status=ToolStatus.AVAILABLE,
            )
        from ploston_core.errors import create_error

        raise create_error("TOOL_UNAVAILABLE", detail=f"Tool '{name}' not found")

    registry.get_or_raise.side_effect = get_or_raise

    def get_router(name):
        if name == "slack__post_message":
            return ToolRouter(source=ToolSource.MCP, server_name="slack")
        return None

    registry.get_router.side_effect = get_router
    return registry


@pytest.fixture
def tool_registry_with_github() -> MagicMock:
    """ToolRegistry that DOES have github registered on CP."""
    registry = MagicMock()
    github_tool = ToolDefinition(
        name="github__actions_list",
        description="List GitHub Actions",
        source=ToolSource.MCP,
        server_name="github",
        status=ToolStatus.AVAILABLE,
    )
    registry.list_tools.return_value = [github_tool]
    registry.get_or_raise.return_value = github_tool
    registry.get_router.return_value = ToolRouter(source=ToolSource.MCP, server_name="github")
    return registry


class TestToolResolverIntegration:
    """I-01 through I-03: End-to-end tool resolution."""

    @pytest.mark.asyncio
    async def test_i01_runner_tool_executes_when_connected(
        self, runner_registry, tool_registry_no_github
    ):
        """I-01: Runner tool executes successfully when runner is connected."""
        dispatcher = RunnerToolDispatcher(runner_registry)

        # Mock the actual WebSocket call
        from unittest.mock import patch

        with patch(
            "ploston_core.api.routers.runner_static.send_tool_call_to_runner",
            new_callable=AsyncMock,
            return_value={"runs": [{"id": 1, "name": "CI"}]},
        ):
            invoker = ToolInvoker(
                tool_registry=tool_registry_no_github,
                mcp_manager=MagicMock(),
                sandbox_factory=MagicMock(),
                runner_dispatcher=dispatcher,
            )
            result = await invoker.invoke(
                "macbook-pro-local__github__actions_list",
                {"repo": "test-repo"},
            )

        assert isinstance(result, ToolCallResult)
        assert result.success is True
        assert result.output == {"runs": [{"id": 1, "name": "CI"}]}

    @pytest.mark.asyncio
    async def test_i02_fails_with_tool_unavailable_when_disconnected(
        self, runner_registry, tool_registry_no_github
    ):
        """I-02: Returns TOOL_UNAVAILABLE (not INTERNAL_ERROR) when runner is disconnected."""
        runner = runner_registry.get_by_name("macbook-pro-local")
        runner_registry.set_disconnected(runner.id)

        dispatcher = RunnerToolDispatcher(runner_registry)
        invoker = ToolInvoker(
            tool_registry=tool_registry_no_github,
            mcp_manager=MagicMock(),
            sandbox_factory=MagicMock(),
            runner_dispatcher=dispatcher,
        )
        result = await invoker.invoke("macbook-pro-local__github__actions_list", {})

        assert result.success is False
        assert isinstance(result.error, AELError)
        assert result.error.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_i03_cp_tool_uses_cp_path(self, tool_registry_with_github):
        """I-03: Non-prefixed tool name uses CP path, not runner dispatch."""
        mcp_result = MagicMock()
        mcp_result.is_error = False
        mcp_result.content = {"runs": [{"id": 1}]}
        mcp_result.structured_content = None
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value=mcp_result)

        invoker = ToolInvoker(
            tool_registry=tool_registry_with_github,
            mcp_manager=mock_mcp,
            sandbox_factory=MagicMock(),
            runner_dispatcher=MagicMock(),  # should NOT be called
        )
        result = await invoker.invoke("github__actions_list", {"repo": "test"})
        assert result.success is True
        # The MCP manager was called, not the runner dispatcher
        mock_mcp.call_tool.assert_called_once()
