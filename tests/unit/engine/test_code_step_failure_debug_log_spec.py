"""Spec test for code-step debug_log on the FAILURE path (L-10).

L-10: for a code step, the ``debug_log`` (context.log() output captured in the
sandbox) was discarded on the failure path — exactly when it is most useful.
The fix attaches the captured ``debug_log`` to the failed step's
``error_metadata`` before raising, so failures carry the debug output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.engine.types import ExecutionContext
from ploston_core.errors import create_error
from ploston_core.types import StepStatus, StepType


def _make_failing_code_engine(debug_lines: list[str]) -> WorkflowEngine:
    """Engine whose python_exec invoke logs ``debug_lines`` then fails."""
    tool_invoker = MagicMock()

    async def _invoke(*, tool_name: str, params: dict, timeout_seconds=None):
        # Mirror real python_exec: emit context.log() entries into the sandbox
        # debug buffer, then fail.
        if tool_name == "python_exec":
            sandbox_ctx = params["context"]
            for line in debug_lines:
                sandbox_ctx.log(line)
        result = MagicMock()
        result.success = False
        result.output = None
        result.error = create_error("CODE_RUNTIME", step_id="diagnose")
        return result

    tool_invoker.invoke = AsyncMock(side_effect=_invoke)

    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=tool_invoker,
        template_engine=MagicMock(),
        config=MagicMock(default_timeout=30),
        max_tool_calls=10,
    )


def _code_step() -> MagicMock:
    step = MagicMock()
    step.id = "diagnose"
    step.step_type = StepType.CODE
    step.code = "context.log('starting')\nraise RuntimeError('boom')"
    step.tool = None
    step.mcp = None
    step.params = None
    step.when = None
    step.timeout = None
    step.on_error = None
    step.retry = None
    step.depends_on = None
    return step


def _ctx() -> ExecutionContext:
    wf = MagicMock()
    wf.name = "wf"
    wf.version = "1"
    wf.defaults = None
    return ExecutionContext(
        execution_id="exec-1",
        workflow=wf,
        inputs={},
        config={},
    )


async def test_failed_code_step_carries_debug_log_in_error_metadata():
    debug_lines = ["starting", "about to divide", "value=0"]
    engine = _make_failing_code_engine(debug_lines)
    step = _code_step()

    result = await engine._execute_step_once(step, _ctx(), step_index=0, total_steps=1)

    assert result.status == StepStatus.FAILED
    assert result.error_metadata is not None
    assert result.error_metadata.get("debug_log") == debug_lines


async def test_failed_code_step_without_debug_output_omits_empty_debug_log():
    """No log() calls → no noisy empty debug_log key on the metadata."""
    engine = _make_failing_code_engine([])
    step = _code_step()

    result = await engine._execute_step_once(step, _ctx(), step_index=0, total_steps=1)

    assert result.status == StepStatus.FAILED
    assert result.error_metadata is not None
    assert "debug_log" not in result.error_metadata
