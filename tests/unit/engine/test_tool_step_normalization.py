"""S-270 TS-01..TS-04: normalization applied at tool step and sandbox call sites."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.sandbox.types import RunnerContext, ToolCallInterface

# ── TS-01: _execute_tool_step normalizes tool output ──


@pytest.fixture
def engine():
    invoker = MagicMock()
    invoker.invoke = AsyncMock()
    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=invoker,
        template_engine=MagicMock(),
        config=MagicMock(step_timeout=30),
        tool_registry=MagicMock(list_tools=MagicMock(return_value=[])),
        runner_registry=MagicMock(list=MagicMock(return_value=[])),
    )


@pytest.fixture
def ctx():
    c = MagicMock()
    c.get_template_context.return_value = {}
    c.workflow.defaults = None
    return c


@pytest.fixture
def step():
    s = MagicMock()
    s.id = "fetch"
    s.tool = "github__actions_list"
    s.mcp = None
    s.params = {}
    return s


@pytest.mark.asyncio
async def test_ts01_tool_step_output_normalized(engine, ctx, step):
    """Triple-wrap transport envelope is stripped before return."""
    invoke_result = MagicMock()
    invoke_result.success = True
    invoke_result.output = {
        "status": "success",
        "result": {"content": {"workflow_runs": [{"id": 1}]}},
    }
    engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)
    engine._template_engine.render_params = MagicMock(return_value={})

    output = await engine._execute_tool_step(step, ctx)
    assert output == {"workflow_runs": [{"id": 1}]}


@pytest.mark.asyncio
async def test_ts01b_already_normalized_passes_through(engine, ctx, step):
    invoke_result = MagicMock()
    invoke_result.success = True
    invoke_result.output = {"workflow_runs": [{"id": 1}]}
    engine._tool_invoker.invoke = AsyncMock(return_value=invoke_result)
    engine._template_engine.render_params = MagicMock(return_value={})

    output = await engine._execute_tool_step(step, ctx)
    assert output == {"workflow_runs": [{"id": 1}]}


# ── TS-02/03/04: ToolCallInterface normalizes at call/call_mcp sites ──


def _build_tool_iface(caller_result, tool_registry=None, runner_context=None):
    caller = MagicMock()
    caller.call = AsyncMock(return_value=caller_result)
    return ToolCallInterface(
        tool_caller=caller,
        max_calls=5,
        tool_registry=tool_registry,
        runner_context=runner_context,
    )


@pytest.mark.asyncio
async def test_ts02_call_mcp_cp_direct_normalizes():
    """TS-02: CP-direct call_mcp path returns normalized output."""
    tool_def = MagicMock()
    tool_def.name = "list_commits"
    tr = MagicMock()
    tr.list_tools.return_value = [tool_def]

    iface = _build_tool_iface(
        caller_result={"status": "success", "result": {"content": {"commits": [1, 2]}}},
        tool_registry=tr,
    )
    out = await iface.call_mcp("github", "list_commits", {})
    assert out == {"commits": [1, 2]}


@pytest.mark.asyncio
async def test_ts03_call_mcp_runner_path_normalizes():
    """TS-03: runner-hosted call_mcp path returns normalized output."""
    iface = _build_tool_iface(
        caller_result=[{"type": "text", "text": '{"x": 1}'}],
        runner_context=RunnerContext(defaults_runner="my_runner", runner_name=None),
    )
    out = await iface.call_mcp("github", "get_file", {})
    assert out == {"x": 1}


@pytest.mark.asyncio
async def test_ts04_call_canonical_path_normalizes():
    """TS-04: canonical call() path returns normalized output."""
    iface = _build_tool_iface(
        caller_result={"status": "success", "result": {"data": 42}},
    )
    out = await iface.call("my_runner__github__get_file", {})
    assert out == {"data": 42}


@pytest.mark.asyncio
async def test_call_preserves_bare_result_with_siblings():
    """Regression: bare {result, warnings} tool response is not stripped."""
    iface = _build_tool_iface(
        caller_result={"result": [1, 2], "warnings": ["w"]},
    )
    out = await iface.call("my_tool", {})
    assert out == {"result": [1, 2], "warnings": ["w"]}
