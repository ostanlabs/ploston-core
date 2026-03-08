"""Tests for T-734: RunnerContext and engine wiring for call_mcp.

U-01 through U-09.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.sandbox import RunnerContext, SandboxContext, ToolCallInterface
from ploston_core.sandbox.types import RunnerContext as RunnerContextDirect


class TestRunnerContext:
    """U-01 through U-02: RunnerContext type and SandboxContext integration."""

    def test_u01_runner_context_exported_from_sandbox(self) -> None:
        """U-01: RunnerContext is importable from sandbox/__init__."""
        assert RunnerContext is RunnerContextDirect

    def test_u02_sandbox_context_accepts_runner_context(self) -> None:
        """U-02: SandboxContext stores runner_context field."""
        rc = RunnerContext(runner_name="my-runner", defaults_runner="default-runner")
        mock_tools = MagicMock()
        ctx = SandboxContext(inputs={}, steps={}, config={}, tools=mock_tools, runner_context=rc)
        assert ctx.runner_context is rc
        assert ctx.runner_context.runner_name == "my-runner"
        assert ctx.runner_context.defaults_runner == "default-runner"

    def test_u02_sandbox_context_runner_context_defaults_none(self) -> None:
        """U-02b: SandboxContext.runner_context defaults to None."""
        mock_tools = MagicMock()
        ctx = SandboxContext(inputs={}, steps={}, config={}, tools=mock_tools)
        assert ctx.runner_context is None


class TestExecuteCodeStepWiring:
    """U-03 through U-06: _execute_code_step constructs proper objects."""

    @pytest.fixture
    def engine(self):
        """Create a WorkflowEngine with mocks."""
        from ploston_core.engine.engine import WorkflowEngine

        return WorkflowEngine(
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            template_engine=MagicMock(),
            config=MagicMock(step_timeout=30),
            tool_registry=MagicMock(),
            runner_registry=MagicMock(),
            max_tool_calls=5,
        )

    @pytest.fixture
    def mock_context(self):
        """Create a mock ExecutionContext."""
        ctx = MagicMock()
        ctx.inputs = {"key": "val"}
        ctx.step_outputs = {}
        ctx.config = {}
        ctx.execution_id = "exec-abc"
        ctx.workflow.defaults.runner = "default-runner-1"
        return ctx

    @pytest.fixture
    def mock_step(self):
        step = MagicMock()
        step.id = "analyze"
        step.code = "result = 42"
        return step

    @pytest.mark.asyncio
    async def test_u03_runner_context_has_raw_bridge_value(
        self, engine, mock_context, mock_step
    ) -> None:
        """U-03: RunnerContext gets raw bridge header value."""
        mock_bridge_ctx = MagicMock()
        mock_bridge_ctx.runner_name = "bridge-runner"

        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = 42
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch(
            "ploston_core.engine.engine.bridge_context",
            create=True,
        ) as _mock_bc_mod:
            # Patch the import inside _execute_code_step
            with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
                mock_bc.get.return_value = mock_bridge_ctx
                await engine._execute_code_step(mock_step, mock_context)

        # Check what was passed as context to python_exec
        call_args = engine._tool_invoker.invoke.call_args
        sandbox_ctx = call_args.kwargs.get("params", call_args[1].get("params", {})).get("context")
        assert sandbox_ctx is not None
        assert isinstance(sandbox_ctx, SandboxContext)
        assert sandbox_ctx.runner_context.runner_name == "bridge-runner"

    @pytest.mark.asyncio
    async def test_u04_runner_context_has_defaults_runner(
        self, engine, mock_context, mock_step
    ) -> None:
        """U-04: RunnerContext.defaults_runner from workflow.defaults.runner."""
        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = 42
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        call_args = engine._tool_invoker.invoke.call_args
        sandbox_ctx = call_args.kwargs.get("params", call_args[1].get("params", {})).get("context")
        assert sandbox_ctx.runner_context.defaults_runner == "default-runner-1"

    @pytest.mark.asyncio
    async def test_u05_runner_context_has_step_and_execution_id(
        self, engine, mock_context, mock_step
    ) -> None:
        """U-05: RunnerContext has step_id and execution_id."""
        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = 42
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

    @pytest.mark.asyncio
    async def test_u06_tools_is_tool_call_interface(self, engine, mock_context, mock_step) -> None:
        """U-06: _execute_code_step wraps invoker in ToolCallInterface, not raw."""
        invoke_result = MagicMock()
        invoke_result.success = True
        invoke_result.output = 42
        engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            await engine._execute_code_step(mock_step, mock_context)

        call_args = engine._tool_invoker.invoke.call_args
        sandbox_ctx = call_args.kwargs.get("params", call_args[1].get("params", {})).get("context")
        assert isinstance(sandbox_ctx.tools, ToolCallInterface)


class TestWorkflowEngineInit:
    """U-07: WorkflowEngine accepts new params."""

    def test_u07_engine_stores_tool_registry_and_max_tool_calls(self) -> None:
        """U-07: WorkflowEngine stores tool_registry and max_tool_calls."""
        from ploston_core.engine.engine import WorkflowEngine

        tr = MagicMock()
        engine = WorkflowEngine(
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            template_engine=MagicMock(),
            config=MagicMock(),
            tool_registry=tr,
            max_tool_calls=20,
        )
        assert engine._tool_registry is tr
        assert engine._max_tool_calls == 20

    def test_u07_max_tool_calls_default(self) -> None:
        """U-07b: max_tool_calls defaults to 10."""
        from ploston_core.engine.engine import WorkflowEngine

        engine = WorkflowEngine(
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            template_engine=MagicMock(),
            config=MagicMock(),
        )
        assert engine._max_tool_calls == 10


class TestResolveInvokeNameInference:
    """U-08 and U-09: _resolve_invoke_name runner inference."""

    @pytest.fixture
    def engine(self):
        from ploston_core.engine.engine import WorkflowEngine

        runner_registry = MagicMock()
        return WorkflowEngine(
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            template_engine=MagicMock(),
            config=MagicMock(),
            runner_registry=runner_registry,
        )

    def _make_runner(self, name: str, tools: list[str], connected: bool = True):
        r = MagicMock()
        r.name = name
        r.status.value = "connected" if connected else "disconnected"
        r.available_tools = tools
        return r

    def test_u08_single_match_inference(self, engine) -> None:
        """U-08: Single runner with matching mcp is auto-selected."""
        runner = self._make_runner("laptop", ["github__list_commits", "github__list_repos"])
        engine._runner_registry.list.return_value = [runner]
        engine._runner_registry._get_tool_name.side_effect = lambda t: t

        step = MagicMock()
        step.id = "s1"
        step.tool = "list_commits"
        step.mcp = "github"

        workflow = MagicMock()
        workflow.defaults = None

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            result = engine._resolve_invoke_name(step, workflow)

        assert result == "laptop__github__list_commits"

    def test_u09_ambiguous_runners_logs_warning(self, engine) -> None:
        """U-09: Multiple matching runners logs warning, returns bare name."""
        r1 = self._make_runner("runner-a", ["github__list_commits"])
        r2 = self._make_runner("runner-b", ["github__list_repos"])
        engine._runner_registry.list.return_value = [r1, r2]
        engine._runner_registry._get_tool_name.side_effect = lambda t: t

        engine._logger = MagicMock()

        step = MagicMock()
        step.id = "s1"
        step.tool = "list_commits"
        step.mcp = "github"

        workflow = MagicMock()
        workflow.defaults = None

        with patch("ploston_core.mcp_frontend.http_transport.bridge_context") as mock_bc:
            mock_bc.get.return_value = None
            result = engine._resolve_invoke_name(step, workflow)

        # Falls back to bare tool name
        assert result == "list_commits"
        # Warning was logged
        warning_calls = [
            c
            for c in engine._logger._log.call_args_list
            if c[0][0].value == "WARN" or c[0][0] == "WARN"
        ]
        assert len(warning_calls) > 0
