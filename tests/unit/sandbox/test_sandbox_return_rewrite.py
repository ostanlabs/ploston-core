"""Tests for S-293 / DEC-189: top-level ``return`` rewrite in code steps.

Covers ``_ReturnRewriter`` and the surrounding sandbox integration:

  * top-level ``return X`` sets ``result`` to ``X`` and exits the step
  * top-level ``return`` (no value) exits with ``result`` unchanged
  * guard-clause patterns short-circuit before later statements run
  * nested ``return`` inside ``def``/``async def`` keeps Python semantics
  * ``return result`` short-circuits to a bare raise (no self-assignment)
  * ``finally`` blocks still run when the step exits via ``return``
  * the rewriter exit signal does not bubble out as a generic Exception
  * line numbers in tracebacks survive the AST transform
"""

from __future__ import annotations

import pytest

from ploston_core.sandbox.sandbox import PythonExecSandbox


@pytest.fixture
def sandbox() -> PythonExecSandbox:
    return PythonExecSandbox()


class TestSandboxReturnRewrite:
    @pytest.mark.asyncio
    async def test_top_level_return_value_sets_result(self, sandbox: PythonExecSandbox):
        r = await sandbox.execute("return 42")
        assert r.success and r.error is None
        assert r.result == 42

    @pytest.mark.asyncio
    async def test_top_level_bare_return_keeps_result_none(self, sandbox: PythonExecSandbox):
        r = await sandbox.execute("return")
        assert r.success and r.error is None
        assert r.result is None

    @pytest.mark.asyncio
    async def test_top_level_bare_return_preserves_prior_assignment(
        self, sandbox: PythonExecSandbox
    ):
        # Bare ``return`` after ``result = ...`` keeps the assigned value.
        r = await sandbox.execute("result = 7\nreturn")
        assert r.success and r.error is None
        assert r.result == 7

    @pytest.mark.asyncio
    async def test_guard_clause_short_circuits(self, sandbox: PythonExecSandbox):
        code = "x = 0\nif x == 0:\n    return 'early'\nresult = 'late'\n"
        r = await sandbox.execute(code)
        assert r.success and r.error is None
        assert r.result == "early"

    @pytest.mark.asyncio
    async def test_return_inside_def_is_not_rewritten(self, sandbox: PythonExecSandbox):
        code = "def helper(n):\n    return n * 2\nresult = helper(21)\n"
        r = await sandbox.execute(code)
        assert r.success and r.error is None
        assert r.result == 42

    @pytest.mark.asyncio
    async def test_return_inside_async_def_is_not_rewritten(self, sandbox: PythonExecSandbox):
        code = (
            "async def helper():\n"
            "    return 'inner'\n"
            "import asyncio  # not allowed; use a coroutine via await\n"
        )
        # The above would fail on ``import asyncio``; instead, exercise
        # the more typical path: define an async helper, then top-level
        # ``return`` consumes its coroutine via await.
        code = "async def helper():\n    return 'inner'\nvalue = await helper()\nreturn value\n"
        r = await sandbox.execute(code)
        assert r.success and r.error is None
        assert r.result == "inner"

    @pytest.mark.asyncio
    async def test_return_result_self_assign_optimisation(self, sandbox: PythonExecSandbox):
        # ``return result`` should be equivalent to a bare exit — never
        # re-evaluate the RHS, never produce a stale temporary.
        code = "result = {'x': 1}\nreturn result\n"
        r = await sandbox.execute(code)
        assert r.success and r.error is None
        assert r.result == {"x": 1}

    @pytest.mark.asyncio
    async def test_finally_runs_on_return(self, sandbox: PythonExecSandbox):
        code = (
            "trace = []\n"
            "try:\n"
            "    trace.append('try')\n"
            "    return 'rv'\n"
            "finally:\n"
            "    trace.append('finally')\n"
            "    result = trace  # overridden by the early-set result\n"
        )
        r = await sandbox.execute(code)
        assert r.success and r.error is None
        # result was set to 'rv' by the rewritten return; the finally
        # block re-assigns ``result`` to the trace list, so the final
        # value reflects the finally-clause re-assignment.
        assert r.result == ["try", "finally"]

    @pytest.mark.asyncio
    async def test_step_exit_does_not_surface_as_error(self, sandbox: PythonExecSandbox):
        # The internal _PlostonStepExit must never reach the user as an
        # error string; success=True with the rewritten ``result``.
        r = await sandbox.execute("return None")
        assert r.success
        assert r.error is None
        assert r.result is None

    @pytest.mark.asyncio
    async def test_line_number_preserved_on_runtime_error(self, sandbox: PythonExecSandbox):
        # A runtime error after a guard-clause return must report the
        # correct source line. The rewriter copies locations onto the
        # injected nodes, so a NameError on line 3 stays at line 3.
        code = "x = 1\nif x == 0:\n    return 'early'\ny = undefined_name  # line 4\n"
        r = await sandbox.execute(code)
        assert not r.success
        assert r.error is not None
        assert "line 4" in r.error
