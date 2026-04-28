"""S-270 TS-01..TS-04: normalization applied at tool step and sandbox call sites.

S-289 P1 extends this with ToolError raising on transport-level error envelopes
(see test_te01..te05 below).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.sandbox.types import RunnerContext, ToolCallInterface, ToolError

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


# ── S-289 P1: ToolError on {"content": X, "error": E} envelope ──


@pytest.mark.asyncio
async def test_te01_call_raises_tool_error_on_envelope():
    """call() raises ToolError when the response envelope has non-null error."""
    iface = _build_tool_iface(
        caller_result={"content": None, "error": "tool blew up"},
    )
    with pytest.raises(ToolError) as excinfo:
        await iface.call("my_runner__github__get_file", {})
    assert excinfo.value.message == "tool blew up"
    assert excinfo.value.tool == "my_runner__github__get_file"
    assert excinfo.value.mcp is None


@pytest.mark.asyncio
async def test_te02_call_mcp_cp_direct_raises_tool_error():
    """CP-direct call_mcp raises ToolError on transport error and tags mcp/tool."""
    tool_def = MagicMock()
    tool_def.name = "list_commits"
    tr = MagicMock()
    tr.list_tools.return_value = [tool_def]

    iface = _build_tool_iface(
        caller_result={"content": None, "error": "rate limited"},
        tool_registry=tr,
    )
    with pytest.raises(ToolError) as excinfo:
        await iface.call_mcp("github", "list_commits", {})
    assert excinfo.value.message == "rate limited"
    assert excinfo.value.mcp == "github"
    assert excinfo.value.tool == "list_commits"


@pytest.mark.asyncio
async def test_te03_call_mcp_runner_path_raises_tool_error():
    """Runner-hosted call_mcp raises ToolError with mcp/tool tags."""
    iface = _build_tool_iface(
        caller_result={"content": None, "error": "upstream 503"},
        runner_context=RunnerContext(defaults_runner="my_runner", runner_name=None),
    )
    with pytest.raises(ToolError) as excinfo:
        await iface.call_mcp("github", "get_file", {})
    assert excinfo.value.message == "upstream 503"
    assert excinfo.value.mcp == "github"
    assert excinfo.value.tool == "get_file"


@pytest.mark.asyncio
async def test_te04_envelope_with_extra_keys_is_not_an_error():
    """Domain payload that happens to have an "error" key is not a transport
    error — must be returned normalized, not raised."""
    iface = _build_tool_iface(
        caller_result={"content": [], "error": "no results", "meta": "x"},
    )
    out = await iface.call("my_tool", {})
    assert out == {"content": [], "error": "no results", "meta": "x"}


@pytest.mark.asyncio
async def test_te05_null_error_envelope_unwrapped_no_raise():
    """{"content": X, "error": None} returns X, never raises."""
    iface = _build_tool_iface(
        caller_result={"content": {"data": 1}, "error": None},
    )
    out = await iface.call("my_tool", {})
    assert out == {"data": 1}


# ── S-289 P1: ToolError is injected into sandbox safe-globals ──


@pytest.mark.asyncio
async def test_te06_tool_error_available_in_sandbox_without_import():
    """Code steps can `except ToolError` without importing it — the name is
    pre-injected into the safe-globals dict alongside `context`."""
    from ploston_core.sandbox import PythonExecSandbox

    sandbox = PythonExecSandbox(timeout=5)
    code = (
        "try:\n"
        "    raise ToolError('boom', tool='t', mcp='m')\n"
        "except ToolError as e:\n"
        "    result = {'message': e.message, 'tool': e.tool, 'mcp': e.mcp}\n"
    )
    res = await sandbox.execute(code)
    assert res.success is True, res.error
    assert res.result == {"message": "boom", "tool": "t", "mcp": "m"}
