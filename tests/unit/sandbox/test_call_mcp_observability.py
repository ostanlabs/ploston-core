"""Tests for T-735 observability: call_mcp and call() logging.

O-01 through O-07.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.sandbox.types import RunnerContext, ToolCallInterface
from ploston_core.types import LogLevel


def _make_interface(**kwargs) -> tuple[ToolCallInterface, AsyncMock, MagicMock]:
    """Helper: returns (interface, caller_mock, logger_mock)."""
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"ok": True})
    logger = MagicMock()
    iface = ToolCallInterface(
        tool_caller=caller,
        max_calls=kwargs.get("max_calls", 10),
        logger=logger,
        runner_context=kwargs.get("runner_context"),
        tool_registry=kwargs.get("tool_registry"),
        runner_registry=kwargs.get("runner_registry"),
    )
    return iface, caller, logger


class TestCallObservability:
    """O-01, O-02: call() normalized logging."""

    @pytest.mark.asyncio
    async def test_o01_call_logs_normalized_tool_name(self) -> None:
        """O-01: call() logs metric_name without runner prefix."""
        rc = RunnerContext(step_id="s1", execution_id="e1")
        iface, _, logger = _make_interface(runner_context=rc)

        # Call with a runner-prefixed canonical name
        await iface.call("laptop__github__list_commits", {})

        # First INFO log should use normalized name
        info_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.INFO]
        assert len(info_calls) >= 1
        first_info = info_calls[0]
        log_data = first_info[0][3]
        assert log_data["tool_name"] == "github__list_commits"  # stripped runner

    @pytest.mark.asyncio
    async def test_o02_call_logs_runner_source_step_execution(self) -> None:
        """O-02: call() logs runner, source, step_id, execution_id."""
        rc = RunnerContext(step_id="step-x", execution_id="exec-y")
        iface, _, logger = _make_interface(runner_context=rc)

        await iface.call("laptop__github__list_commits", {})

        info_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.INFO]
        first_info = info_calls[0]
        log_data = first_info[0][3]
        assert log_data["runner"] == "laptop"
        assert log_data["source"] == "sandbox"
        assert log_data["step_id"] == "step-x"
        assert log_data["execution_id"] == "exec-y"


class TestCallMcpObservability:
    """O-03, O-04: call_mcp() logging."""

    @pytest.mark.asyncio
    async def test_o03_call_mcp_cp_path_logs(self) -> None:
        """O-03: call_mcp CP path logs tool_name, source=cp, runner=None."""
        tool_def = MagicMock()
        tool_def.name = "list_commits"
        tr = MagicMock()
        tr.list_tools.return_value = [tool_def]

        rc = RunnerContext(step_id="s1", execution_id="e1")
        iface, _, logger = _make_interface(tool_registry=tr, runner_context=rc)

        await iface.call_mcp("github", "list_commits", {})

        info_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.INFO]
        assert len(info_calls) >= 1
        log_data = info_calls[0][0][3]
        assert log_data["tool_name"] == "github__list_commits"
        assert log_data["source"] == "cp"
        assert log_data["runner"] is None

    @pytest.mark.asyncio
    async def test_o04_call_mcp_runner_path_logs(self) -> None:
        """O-04: call_mcp runner path logs source=runner, runner=<name>."""
        rc = RunnerContext(defaults_runner="laptop", step_id="s1", execution_id="e1")
        iface, _, logger = _make_interface(runner_context=rc)

        await iface.call_mcp("github", "list_commits", {})

        info_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.INFO]
        assert len(info_calls) >= 1
        log_data = info_calls[0][0][3]
        assert log_data["source"] == "runner"
        assert log_data["runner"] == "laptop"


class TestRateLimitObservability:
    """O-05, O-06: Rate limit warning logs."""

    @pytest.mark.asyncio
    async def test_o05_rate_limit_warning_fields(self) -> None:
        """O-05: Rate limit warning has step_id, execution_id, event."""
        from ploston_core.errors.errors import AELError

        rc = RunnerContext(step_id="s1", execution_id="e1")
        iface, _, logger = _make_interface(runner_context=rc, max_calls=0)

        with pytest.raises(AELError):
            await iface.call("some_tool", {})

        warn_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.WARN]
        assert len(warn_calls) == 1
        log_data = warn_calls[0][0][3]
        assert log_data["event"] == "sandbox_rate_limit_exhausted"
        assert log_data["step_id"] == "s1"
        assert log_data["execution_id"] == "e1"

    @pytest.mark.asyncio
    async def test_o06_step_and_execution_id_match(self) -> None:
        """O-06: step_id and execution_id in logs match RunnerContext."""
        rc = RunnerContext(step_id="mystep", execution_id="myexec")
        iface, _, logger = _make_interface(runner_context=rc)

        await iface.call("tool_a", {})

        info_calls = [c for c in logger._log.call_args_list if c[0][0] == LogLevel.INFO]
        log_data = info_calls[0][0][3]
        assert log_data["step_id"] == "mystep"
        assert log_data["execution_id"] == "myexec"


class TestNoDoubleInstrumentation:
    """O-07: No second instrument_tool_call span."""

    @pytest.mark.asyncio
    async def test_o07_no_instrument_tool_call_in_interface(self) -> None:
        """O-07: ToolCallInterface does not emit instrument_tool_call spans."""
        import ploston_core.sandbox.types as types_mod

        # Verify that instrument_tool_call is not referenced in the module
        source = types_mod.__file__
        with open(source) as f:
            content = f.read()
        assert "instrument_tool_call" not in content
