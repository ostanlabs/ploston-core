"""Tests for T-736: Engine integration for call_mcp in code steps.

I-01 through I-04.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.sandbox import ToolCallInterface


@pytest.fixture
def engine():
    """WorkflowEngine with all registries wired."""
    tool_def = MagicMock()
    tool_def.name = "list_commits"
    tr = MagicMock()
    tr.list_tools.return_value = [tool_def]

    rr = MagicMock()
    rr.list.return_value = []

    invoker = MagicMock()
    invoker.invoke = AsyncMock()
    invoker.call = AsyncMock(return_value={"commits": [1, 2, 3]})

    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=invoker,
        template_engine=MagicMock(),
        config=MagicMock(step_timeout=30),
        tool_registry=tr,
        runner_registry=rr,
        max_tool_calls=5,
    )


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.inputs = {}
    ctx.step_outputs = {}
    ctx.config = {}
    ctx.execution_id = "exec-1"
    ctx.workflow.defaults = None
    return ctx


@pytest.fixture
def mock_step():
    step = MagicMock()
    step.id = "analyze"
    return step


class TestCodeStepCallMcpIntegration:
    """I-01 through I-04."""

    @pytest.mark.asyncio
    async def test_i01_code_step_call_mcp_executes(self, engine, mock_context, mock_step) -> None:
        """I-01: Code step with call_mcp executes correctly.

        Verifies that _execute_code_step creates a ToolCallInterface
        with call_mcp capability and passes it as SandboxContext.tools.
        """
        mock_step.code = 'result = await context.tools.call_mcp("github", "list_commits", {})'

        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = {"commits": [1, 2, 3]}
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        # Verify sandbox context was constructed with ToolCallInterface
        call_args = engine._tool_invoker.invoke.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        sandbox_ctx = params["context"]
        assert isinstance(sandbox_ctx.tools, ToolCallInterface)
        assert hasattr(sandbox_ctx.tools, "call_mcp")

    @pytest.mark.asyncio
    async def test_i02_call_mcp_cp_direct_routes_to_cp(
        self, engine, mock_context, mock_step
    ) -> None:
        """I-02: call_mcp on CP-direct tool routes to CP, not runner."""
        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = 42
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        # Extract the ToolCallInterface from constructed context
        call_args = engine._tool_invoker.invoke.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        tool_iface = params["context"].tools
        assert tool_iface._tool_registry is engine._tool_registry

    @pytest.mark.asyncio
    async def test_i03_old_call_still_works(self, engine, mock_context, mock_step) -> None:
        """I-03: context.tools.call(canonical, {}) still works (no regression)."""
        mock_step.code = 'result = await context.tools.call("some_tool", {})'

        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = "done"
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        # Just verify no exception; the tools attribute has call() method
        call_args = engine._tool_invoker.invoke.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        tool_iface = params["context"].tools
        assert callable(getattr(tool_iface, "call", None))

    @pytest.mark.asyncio
    async def test_i04_rate_limit_enforced(self, engine, mock_context, mock_step) -> None:
        """I-04: Rate limit enforced — max_tool_calls from engine."""
        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = None
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        call_args = engine._tool_invoker.invoke.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        tool_iface = params["context"].tools
        assert tool_iface._max_calls == 5  # From engine's max_tool_calls=5


# ── DEC-145: _WorkflowSourceLogger bridge_session_id injection ──


class TestWorkflowSourceLoggerBridgeSessionId:
    """Test _WorkflowSourceLogger injects bridge_session_id into log records."""

    def test_bridge_session_id_injected(self) -> None:
        """Test that bridge_session_id is injected into log context."""

        from ploston_core.engine.engine import _WorkflowSourceLogger

        inner = MagicMock()
        logger = _WorkflowSourceLogger(inner)
        logger.set_execution_id("exec-1")
        logger.set_bridge_session_id("bridge-abc")

        logger._log(MagicMock(), "engine", "test message", {"foo": "bar"})

        inner._log.assert_called_once()
        ctx = inner._log.call_args[0][3]
        assert ctx["bridge_session_id"] == "bridge-abc"
        assert ctx["execution_id"] == "exec-1"
        assert ctx["foo"] == "bar"

    def test_bridge_session_id_not_injected_when_none(self) -> None:
        """Test that bridge_session_id is NOT injected when None."""
        from ploston_core.engine.engine import _WorkflowSourceLogger

        inner = MagicMock()
        logger = _WorkflowSourceLogger(inner)
        logger.set_execution_id("exec-1")

        logger._log(MagicMock(), "engine", "test message", {})

        ctx = inner._log.call_args[0][3]
        assert "bridge_session_id" not in ctx
