"""CR-1: process-isolation + hard-kill timeout for the code-step sandbox.

The sandbox executes each code step in a CHILD PROCESS so that CPU-bound /
blocking *synchronous* code (``while True: pass``) can be interrupted by the
PARENT via SIGKILL. The previous in-process ``asyncio.wait_for`` design could
not interrupt such code — the event loop never got a chance to fire the
timeout, so the whole process hung.

These tests are the RED→GREEN driver for that change. Every test that could
hang under the old in-process implementation carries a hard
``@pytest.mark.timeout`` so a hang is reported as a *failure*, not an infinite
run.

Covers:
  (a) ``while True: pass`` with timeout=2 returns a timeout failure within ~3s
      and does NOT hang the test process.
  (b) a CPU-heavy busy loop is interrupted by the wall-clock timeout.
  (c) a normal code step that calls a tool via ``context.tools`` still works
      through the IPC bridge back to the parent's real ToolCallInterface.
  (d) the tool rate-limit (max_tool_calls) is still enforced across the IPC
      boundary (a malicious child cannot bypass it).
  (e) ``result`` / top-level ``return`` and ``context.log`` debug_log survive
      the process boundary.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.sandbox.sandbox import PythonExecSandbox
from ploston_core.sandbox.types import (
    RunnerContext,
    SandboxContext,
    ToolCallInterface,
)


def _make_context(tool_interface: ToolCallInterface) -> SandboxContext:
    return SandboxContext(
        inputs={"greeting": "hello"},
        steps={},
        config={},
        tools=tool_interface,
        runner_context=RunnerContext(),
    )


@pytest.mark.security
class TestHardKillTimeout:
    """(a) + (b): the timeout must interrupt CPU-bound / blocking sync code."""

    @pytest.mark.timeout(15)
    @pytest.mark.asyncio
    async def test_infinite_loop_is_killed_within_timeout(self) -> None:
        """``while True: pass`` must abort, not hang the event loop."""
        sandbox = PythonExecSandbox(timeout=2)

        start = time.perf_counter()
        result = await sandbox.execute("while True:\n    pass\n", {})
        elapsed = time.perf_counter() - start

        assert result.success is False
        assert result.error is not None
        assert "timeout" in result.error.lower()
        # Hard-kill should land well within a few seconds of the 2s budget.
        assert elapsed < 8, f"took {elapsed:.1f}s — timeout did not hard-kill"

    @pytest.mark.timeout(15)
    @pytest.mark.asyncio
    async def test_cpu_heavy_loop_is_interrupted(self) -> None:
        """A CPU-bound counting loop with no awaits is interrupted."""
        sandbox = PythonExecSandbox(timeout=2)
        code = "n = 0\nwhile n < 10**18:\n    n += 1\nresult = n\n"

        start = time.perf_counter()
        result = await sandbox.execute(code, {})
        elapsed = time.perf_counter() - start

        assert result.success is False
        assert result.error is not None
        assert "timeout" in result.error.lower()
        assert elapsed < 8, f"took {elapsed:.1f}s — CPU loop not interrupted"


@pytest.mark.security
class TestToolCallIPC:
    """(c): tool calls round-trip from the child back to the parent."""

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_tool_call_via_context_tools_works(self) -> None:
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={"value": 99})
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=10)

        sandbox = PythonExecSandbox(timeout=10)
        code = (
            'data = await context.tools.call("my_tool", {"key": "test"})\nresult = data["value"]\n'
        )
        result = await sandbox.execute(code, {"context": _make_context(tools)})

        assert result.success, f"error: {result.error}"
        assert result.result == 99
        # The REAL caller in the PARENT must have been invoked exactly once.
        mock_caller.call.assert_awaited_once_with("my_tool", {"key": "test"})

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_call_mcp_via_context_tools_works(self) -> None:
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={"ok": True})
        rc = RunnerContext(defaults_runner="laptop")
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=10, runner_context=rc)

        sandbox = PythonExecSandbox(timeout=10)
        code = (
            'res = await context.tools.call_mcp("github", "list_commits", {"repo": "r"})\n'
            "result = res\n"
        )
        result = await sandbox.execute(code, {"context": _make_context(tools)})

        assert result.success, f"error: {result.error}"
        assert result.result == {"ok": True}
        mock_caller.call.assert_awaited_once_with("laptop__github__list_commits", {"repo": "r"})


@pytest.mark.security
class TestRateLimitAcrossBoundary:
    """(d): max_tool_calls is enforced on the PARENT side."""

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_rate_limit_enforced_through_ipc(self) -> None:
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={"v": 1})
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=2)

        sandbox = PythonExecSandbox(timeout=10)
        # Third call must be rejected (RESOURCE_EXHAUSTED) regardless of what
        # the child attempts — enforcement lives in the parent.
        code = (
            "ok = 0\n"
            "err = None\n"
            "for i in range(5):\n"
            "    try:\n"
            '        await context.tools.call("t", {})\n'
            "        ok += 1\n"
            "    except Exception as e:\n"
            "        err = str(e)\n"
            "        break\n"
            'result = {"ok": ok, "err": err}\n'
        )
        result = await sandbox.execute(code, {"context": _make_context(tools)})

        assert result.success, f"error: {result.error}"
        assert result.result["ok"] == 2
        assert result.result["err"] is not None
        assert mock_caller.call.await_count == 2

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_blocked_tool_rejected_through_ipc(self) -> None:
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={"v": 1})
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=10)

        sandbox = PythonExecSandbox(timeout=10)
        code = (
            "blocked = False\n"
            "try:\n"
            '    await context.tools.call("python_exec", {"code": "x=1"})\n'
            "except Exception:\n"
            "    blocked = True\n"
            "result = blocked\n"
        )
        result = await sandbox.execute(code, {"context": _make_context(tools)})

        assert result.success, f"error: {result.error}"
        assert result.result is True
        mock_caller.call.assert_not_called()


@pytest.mark.security
class TestResultAndDebugLogPreserved:
    """(e): result/return + debug_log survive the boundary."""

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_return_value_preserved(self) -> None:
        sandbox = PythonExecSandbox(timeout=10)
        result = await sandbox.execute("return {'a': [1, 2, 3]}", {})
        assert result.success, f"error: {result.error}"
        assert result.result == {"a": [1, 2, 3]}

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_stdout_preserved(self) -> None:
        sandbox = PythonExecSandbox(timeout=10)
        result = await sandbox.execute("print('hello from child')\nresult = 1", {})
        assert result.success, f"error: {result.error}"
        assert result.result == 1
        assert "hello from child" in result.stdout

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_debug_log_preserved(self) -> None:
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={})
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=10)
        ctx = _make_context(tools)

        sandbox = PythonExecSandbox(timeout=10)
        code = (
            'context.log("step started")\n'
            'context.log("processing input: " + context.inputs["greeting"])\n'
            "result = 42\n"
        )
        result = await sandbox.execute(code, {"context": ctx})

        assert result.success, f"error: {result.error}"
        assert result.result == 42
        # debug_log written in the child must be visible on the parent's
        # SandboxContext (the engine reads ctx._debug_log after execute()).
        assert ctx._debug_log == [
            "step started",
            "processing input: hello",
        ]

    @pytest.mark.timeout(20)
    @pytest.mark.asyncio
    async def test_tool_error_preserved(self) -> None:
        """ToolError raised on the parent side is catchable in the child."""
        mock_caller = MagicMock()
        # Transport envelope with a non-null error → ToolError inside sandbox.
        mock_caller.call = AsyncMock(return_value={"content": None, "error": "boom"})
        tools = ToolCallInterface(tool_caller=mock_caller, max_calls=10)

        sandbox = PythonExecSandbox(timeout=10)
        code = (
            "caught = None\n"
            "try:\n"
            '    await context.tools.call("t", {})\n'
            "except ToolError as e:\n"
            "    caught = e.message\n"
            "result = caught\n"
        )
        result = await sandbox.execute(code, {"context": _make_context(tools)})

        assert result.success, f"error: {result.error}"
        assert result.result == "boom"
