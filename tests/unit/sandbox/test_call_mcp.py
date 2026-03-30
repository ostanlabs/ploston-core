"""Tests for T-735: call_mcp() on ToolCallInterface.

U-10 through U-23.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.errors.errors import AELError
from ploston_core.sandbox.types import RunnerContext, ToolCallInterface


def _make_interface(
    *,
    tool_registry=None,
    runner_registry=None,
    runner_context=None,
    max_calls=10,
    blocked_tools=None,
) -> tuple[ToolCallInterface, AsyncMock]:
    """Helper to build a ToolCallInterface with a mock caller."""
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"result": "ok"})
    iface = ToolCallInterface(
        tool_caller=caller,
        max_calls=max_calls,
        blocked_tools=blocked_tools,
        tool_registry=tool_registry,
        runner_registry=runner_registry,
        runner_context=runner_context,
    )
    return iface, caller


def _make_runner(name: str, tools: list[str], connected: bool = True):
    r = MagicMock()
    r.name = name
    r.status.value = "connected" if connected else "disconnected"
    r.available_tools = tools
    return r


class TestCallMcpCPDirect:
    """U-10, U-20, U-21: CP-direct resolution."""

    @pytest.mark.asyncio
    async def test_u10_cp_direct_routes_to_bare_name(self) -> None:
        """U-10: CP-direct tool routes to bare name."""
        tool_def = MagicMock()
        tool_def.name = "list_commits"
        tr = MagicMock()
        tr.list_tools.return_value = [tool_def]

        iface, caller = _make_interface(tool_registry=tr)
        result = await iface.call_mcp("github", "list_commits", {"repo": "test"})

        assert result == {"result": "ok"}
        caller.call.assert_awaited_once_with("list_commits", {"repo": "test"})
        tr.list_tools.assert_called_once_with(server_name="github")

    @pytest.mark.asyncio
    async def test_u20_cp_takes_priority_over_runner(self) -> None:
        """U-20: CP-first — tool on both CP and runner, CP wins."""
        tool_def = MagicMock()
        tool_def.name = "list_commits"
        tr = MagicMock()
        tr.list_tools.return_value = [tool_def]

        rc = RunnerContext(runner_name="laptop")
        iface, caller = _make_interface(tool_registry=tr, runner_context=rc)
        await iface.call_mcp("github", "list_commits", {})

        # Bare name (CP-direct), not laptop__github__list_commits
        caller.call.assert_awaited_once_with("list_commits", {})

    @pytest.mark.asyncio
    async def test_u21_no_tool_registry_skips_cp(self) -> None:
        """U-21: No tool_registry skips step 1, goes to runner."""
        rc = RunnerContext(defaults_runner="laptop")
        iface, caller = _make_interface(runner_context=rc)
        await iface.call_mcp("github", "list_commits", {})

        caller.call.assert_awaited_once_with("laptop__github__list_commits", {})


class TestCallMcpBlocking:
    """U-11, U-12: Recursion prevention."""

    @pytest.mark.asyncio
    async def test_u11_python_exec_blocked_on_system_mcp(self) -> None:
        """U-11: call_mcp('system', 'python_exec', {}) is blocked."""
        iface, _ = _make_interface()
        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("system", "python_exec", {})
        assert exc_info.value.code == "TOOL_REJECTED"

    @pytest.mark.asyncio
    async def test_u12_python_exec_blocked_on_any_mcp(self) -> None:
        """U-12: call_mcp('other', 'python_exec', {}) also blocked."""
        iface, _ = _make_interface()
        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("other", "python_exec", {})
        assert exc_info.value.code == "TOOL_REJECTED"


class TestCallMcpRunnerResolution:
    """U-13, U-14, U-15, U-16, U-17, U-22, U-23."""

    @pytest.mark.asyncio
    async def test_u13_defaults_runner_over_runner_name(self) -> None:
        """U-13: defaults_runner takes priority over runner_name."""
        rc = RunnerContext(runner_name="bridge", defaults_runner="default")
        iface, caller = _make_interface(runner_context=rc)
        await iface.call_mcp("github", "list_commits", {})

        caller.call.assert_awaited_once_with("default__github__list_commits", {})

    @pytest.mark.asyncio
    async def test_u14_runner_name_when_no_defaults(self) -> None:
        """U-14: runner_name used when defaults_runner is None."""
        rc = RunnerContext(runner_name="bridge")
        iface, caller = _make_interface(runner_context=rc)
        await iface.call_mcp("github", "list_commits", {})

        caller.call.assert_awaited_once_with("bridge__github__list_commits", {})

    @pytest.mark.asyncio
    async def test_u15_single_match_inference(self) -> None:
        """U-15: Single runner with matching MCP is auto-selected."""
        runner = _make_runner("laptop", ["github__list_commits"])
        rr = MagicMock()
        rr.list.return_value = [runner]
        rr._get_tool_name.side_effect = lambda t: t

        iface, caller = _make_interface(runner_registry=rr)
        await iface.call_mcp("github", "list_commits", {})

        caller.call.assert_awaited_once_with("laptop__github__list_commits", {})

    @pytest.mark.asyncio
    async def test_u16_ambiguous_runners_raises(self) -> None:
        """U-16: Multiple runners hosting same MCP raises TOOL_UNAVAILABLE."""
        r1 = _make_runner("runner-a", ["github__list_commits"])
        r2 = _make_runner("runner-b", ["github__list_repos"])
        rr = MagicMock()
        rr.list.return_value = [r1, r2]
        rr._get_tool_name.side_effect = lambda t: t

        iface, _ = _make_interface(runner_registry=rr)
        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("github", "list_commits", {})
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u17_not_found_raises_unavailable(self) -> None:
        """U-17: No matching tool anywhere raises TOOL_UNAVAILABLE."""
        iface, _ = _make_interface()
        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("github", "list_commits", {})
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u22_no_runner_registry_skips_inference(self) -> None:
        """U-22: No runner_registry skips inference, raises TOOL_UNAVAILABLE."""
        iface, _ = _make_interface()
        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("github", "list_commits", {})
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u23_explicit_runner_arg(self) -> None:
        """U-23: runner= arg overrides all context."""
        rc = RunnerContext(defaults_runner="default", runner_name="bridge")
        iface, caller = _make_interface(runner_context=rc)
        await iface.call_mcp("github", "list_commits", {}, runner="explicit")

        caller.call.assert_awaited_once_with("explicit__github__list_commits", {})


class TestCallMcpRateLimit:
    """U-18, U-19: Rate limiting."""

    @pytest.mark.asyncio
    async def test_u18_call_mcp_counts_against_max_calls(self) -> None:
        """U-18: call_mcp increments _call_count."""
        rc = RunnerContext(defaults_runner="laptop")
        iface, _ = _make_interface(runner_context=rc, max_calls=3)

        await iface.call_mcp("github", "t1", {})
        assert iface._call_count == 1
        await iface.call_mcp("github", "t2", {})
        assert iface._call_count == 2
        await iface.call_mcp("github", "t3", {})
        assert iface._call_count == 3

        with pytest.raises(AELError) as exc_info:
            await iface.call_mcp("github", "t4", {})
        assert exc_info.value.code == "RESOURCE_EXHAUSTED"

    @pytest.mark.asyncio
    async def test_u19_rate_limit_logs_event(self) -> None:
        """U-19: Rate limit exhausted logs event before raising."""
        logger = MagicMock()
        rc = RunnerContext(defaults_runner="laptop", step_id="s1", execution_id="e1")
        iface, _ = _make_interface(runner_context=rc, max_calls=0)
        iface._logger = logger

        with pytest.raises(AELError):
            await iface.call_mcp("github", "tool", {})

        logger._log.assert_called()
        log_args = logger._log.call_args
        assert log_args[0][0].value == "WARN"
        log_data = log_args[0][3]
        assert log_data["event"] == "sandbox_rate_limit_exhausted"
        assert log_data["step_id"] == "s1"
        assert log_data["execution_id"] == "e1"
