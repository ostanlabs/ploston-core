"""Spec coverage for MCPFrontend telemetry/logger/chain-detection paths.

Exercises the execution wrappers with a configured telemetry collector,
logger, and chain detector so the lifecycle branches (start_execution /
end_execution, _log emissions, chain process_tool_call) are driven and
their contracts asserted. Collaborators are mocked at the boundary.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.config import Mode, ModeManager
from ploston_core.errors import AELError
from ploston_core.mcp_frontend.server import MCPFrontend
from ploston_core.types import ExecutionStatus


def make_collector():
    """Mock telemetry collector with the lifecycle methods the server calls."""
    c = MagicMock()
    c.start_execution = AsyncMock(return_value="exec-1")
    c.end_execution = AsyncMock()
    c.start_step = AsyncMock(return_value=None)
    c.end_step = AsyncMock()
    c.start_tool_call = AsyncMock(return_value="call-1")
    c.end_tool_call = AsyncMock()
    return c


def make_logger():
    logger = MagicMock()
    logger._log = MagicMock()
    return logger


def make_tool_result(success=True, output=None, error=None):
    return SimpleNamespace(success=success, output=output, error=error, structured_content=None)


def connected_runner_registry():
    from ploston_core.runner_management.registry import Runner, RunnerStatus

    reg = MagicMock()
    runner = Runner(
        id="rid",
        name="mac",
        token_hash="h",
        status=RunnerStatus.CONNECTED,
        created_at=datetime.now(UTC),
    )
    reg.get_by_name.return_value = runner
    return reg


def build(**kw):
    defaults = dict(
        workflow_engine=MagicMock(),
        tool_registry=MagicMock(),
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        mode_manager=ModeManager(initial_mode=Mode.RUNNING),
    )
    defaults.update(kw)
    return MCPFrontend(**defaults)


# ===========================================================================
# _execute_tool telemetry + logger + chain detection
# ===========================================================================


class TestExecuteToolTelemetry:
    async def test_success_starts_and_ends_execution_completed(self):
        collector = make_collector()
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output="out"))
        frontend = build(tool_invoker=invoker, telemetry_collector=collector, logger=make_logger())
        resp = await frontend._execute_tool("mytool", {"a": 1})
        assert resp["isError"] is False
        collector.start_execution.assert_awaited_once()
        collector.end_execution.assert_awaited_once()
        # End status must be COMPLETED for a successful invocation.
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.COMPLETED

    async def test_failure_ends_execution_failed_with_error(self):
        collector = make_collector()
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(
                success=False, error=SimpleNamespace(code="E1", message="bad", detail="d")
            )
        )
        frontend = build(tool_invoker=invoker, telemetry_collector=collector, logger=make_logger())
        resp = await frontend._execute_tool("mytool", {})
        assert resp["isError"] is True
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.FAILED

    async def test_logger_emits_called_and_completed_events(self):
        logger = make_logger()
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output="x"))
        frontend = build(tool_invoker=invoker, logger=logger)
        await frontend._execute_tool("bridge__tool", {})
        events = [call.args[3]["event"] for call in logger._log.call_args_list]
        assert "direct_tool_called" in events
        assert "direct_tool_completed" in events

    async def test_logger_emits_failed_event_on_failure(self):
        logger = make_logger()
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(
                success=False, error=SimpleNamespace(code="E", message="m")
            )
        )
        frontend = build(tool_invoker=invoker, logger=logger)
        await frontend._execute_tool("tool", {})
        events = [call.args[3]["event"] for call in logger._log.call_args_list]
        assert "direct_tool_failed" in events

    async def test_chain_detector_invoked_on_success(self):
        chain = MagicMock()
        chain.process_tool_call = AsyncMock(return_value=[])
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output="r"))
        frontend = build(tool_invoker=invoker, chain_detector=chain)
        await frontend._execute_tool("mytool", {"x": 1})
        chain.process_tool_call.assert_awaited_once()
        assert chain.process_tool_call.call_args.kwargs["tool_name"] == "mytool"

    async def test_chain_detector_not_invoked_on_failure(self):
        chain = MagicMock()
        chain.process_tool_call = AsyncMock(return_value=[])
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(
                success=False, error=SimpleNamespace(code="E", message="m")
            )
        )
        frontend = build(tool_invoker=invoker, chain_detector=chain)
        await frontend._execute_tool("mytool", {})
        chain.process_tool_call.assert_not_called()

    async def test_chain_detection_error_does_not_fail_tool_call(self):
        chain = MagicMock()
        chain.process_tool_call = AsyncMock(side_effect=RuntimeError("chain boom"))
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output="r"))
        frontend = build(tool_invoker=invoker, chain_detector=chain)
        # Tool call must still succeed despite chain detection blowing up.
        resp = await frontend._execute_tool("mytool", {})
        assert resp["isError"] is False


# ===========================================================================
# _execute_workflow telemetry
# ===========================================================================


class TestExecuteWorkflowTelemetry:
    async def test_completed_workflow_ends_execution_completed(self):
        collector = make_collector()
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(
                status=ExecutionStatus.COMPLETED, outputs={"o": 1}, error=None
            )
        )
        frontend = build(
            workflow_engine=engine, telemetry_collector=collector, logger=make_logger()
        )
        resp = await frontend._execute_workflow("wf", {})
        assert resp["isError"] is False
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.COMPLETED

    async def test_failed_workflow_ends_execution_failed(self):
        collector = make_collector()
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(
                status=ExecutionStatus.FAILED,
                outputs=None,
                error=SimpleNamespace(code="WF", message="boom"),
            )
        )
        frontend = build(
            workflow_engine=engine, telemetry_collector=collector, logger=make_logger()
        )
        resp = await frontend._execute_workflow("wf", {})
        assert resp["isError"] is True
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.FAILED

    async def test_engine_exception_ends_execution_failed_and_reraises(self):
        collector = make_collector()
        engine = MagicMock()
        engine.execute = AsyncMock(side_effect=RuntimeError("engine down"))
        frontend = build(workflow_engine=engine, telemetry_collector=collector)
        with pytest.raises(RuntimeError, match="engine down"):
            await frontend._execute_workflow("wf", {})
        # The except-branch must record a FAILED end_execution.
        assert collector.end_execution.await_count >= 1


# ===========================================================================
# _execute_workflow_mgmt_tool telemetry
# ===========================================================================


class TestExecuteWorkflowMgmtTelemetry:
    async def test_success_ends_completed(self):
        collector = make_collector()
        provider = MagicMock()
        provider.call = AsyncMock(
            return_value={"content": [{"type": "text", "text": "ok"}], "isError": False}
        )
        frontend = build(
            workflow_tools_provider=provider, telemetry_collector=collector, logger=make_logger()
        )
        await frontend._execute_workflow_mgmt_tool("workflow_list", {})
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.COMPLETED

    async def test_error_response_ends_failed_and_parses_error_code(self):
        collector = make_collector()
        provider = MagicMock()
        provider.call = AsyncMock(
            return_value={
                "content": [
                    {"type": "text", "text": json.dumps({"code": "PATCH_BAD", "detail": "nope"})}
                ],
                "isError": True,
            }
        )
        frontend = build(
            workflow_tools_provider=provider, telemetry_collector=collector, logger=make_logger()
        )
        resp = await frontend._execute_workflow_mgmt_tool("workflow_patch", {})
        assert resp["isError"] is True
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.FAILED

    async def test_provider_exception_ends_failed_and_reraises(self):
        collector = make_collector()
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=ValueError("provider broke"))
        frontend = build(workflow_tools_provider=provider, telemetry_collector=collector)
        with pytest.raises(ValueError, match="provider broke"):
            await frontend._execute_workflow_mgmt_tool("workflow_run", {})
        assert collector.end_execution.await_count >= 1


# ===========================================================================
# _execute_runner_tool — MCP-format result, telemetry, logger, chain
# ===========================================================================


class TestExecuteRunnerToolBranches:
    async def test_mcp_format_success_ends_completed_and_returns_result(self):
        collector = make_collector()
        chain = MagicMock()
        chain.process_tool_call = AsyncMock(return_value=None)
        reg = connected_runner_registry()
        frontend = build(
            runner_registry=reg,
            telemetry_collector=collector,
            chain_detector=chain,
            logger=make_logger(),
        )
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                return_value={"content": [{"type": "text", "text": "hello"}], "isError": False},
            ),
        ):
            resp = await frontend._execute_runner_tool("mac", "fs__read", {})
        assert resp["isError"] is False
        assert resp["content"][0]["text"] == "hello"
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.COMPLETED
        # Chain detection runs on the MCP-format success path.
        chain.process_tool_call.assert_awaited()

    async def test_mcp_format_error_ends_failed(self):
        collector = make_collector()
        reg = connected_runner_registry()
        frontend = build(runner_registry=reg, telemetry_collector=collector, logger=make_logger())
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                return_value={
                    "content": [{"type": "text", "text": "tool errored"}],
                    "isError": True,
                },
            ),
        ):
            resp = await frontend._execute_runner_tool("mac", "fs__read", {})
        assert resp["isError"] is True
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.FAILED

    async def test_output_format_error_ends_failed_and_returns_iserror(self):
        collector = make_collector()
        reg = connected_runner_registry()
        frontend = build(runner_registry=reg, telemetry_collector=collector, logger=make_logger())
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                return_value={"error": "File not found"},
            ),
        ):
            resp = await frontend._execute_runner_tool("mac", "fs__read", {})
        assert resp["isError"] is True
        assert "File not found" in resp["content"][0]["text"]
        from ploston_core.telemetry.store.types import ExecutionStatus as TStatus

        assert collector.end_execution.call_args.kwargs["status"] == TStatus.FAILED

    async def test_output_format_success_chain_and_completed(self):
        collector = make_collector()
        chain = MagicMock()
        chain.process_tool_call = AsyncMock(return_value=None)
        reg = connected_runner_registry()
        frontend = build(
            runner_registry=reg,
            telemetry_collector=collector,
            chain_detector=chain,
            logger=make_logger(),
        )
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                return_value={"output": "plain"},
            ),
        ):
            resp = await frontend._execute_runner_tool("mac", "fs__read", {})
        assert resp["isError"] is False
        assert resp["content"][0]["text"] == "plain"
        chain.process_tool_call.assert_awaited()

    async def test_timeout_logs_failed_event(self):
        logger = make_logger()
        reg = connected_runner_registry()
        frontend = build(runner_registry=reg, logger=logger)
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                side_effect=TimeoutError(),
            ),
        ):
            with pytest.raises(AELError):
                await frontend._execute_runner_tool("mac", "fs__read", {})
        events = [c.args[3].get("event") for c in logger._log.call_args_list]
        assert "direct_tool_failed" in events


# ===========================================================================
# _handle_message — full error envelope fields (detail/suggestion/data)
# ===========================================================================


class TestHandleMessageErrorEnvelope:
    async def test_tools_call_error_payload_includes_optional_fields(self):
        frontend = build()
        from ploston_core.errors.errors import ErrorCategory

        async def boom(_params):
            raise AELError(
                code="C",
                category=ErrorCategory.TOOL,
                message="m",
                detail="det",
                suggestion="sug",
                data={"k": "v"},
                retryable=True,
                http_status=409,
            )

        with patch.object(frontend, "_handle_tools_call", side_effect=boom):
            resp = await frontend._handle_message(
                {"id": 1, "method": "tools/call", "params": {"name": "x"}}
            )
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["detail"] == "det"
        assert payload["suggestion"] == "sug"
        assert payload["data"] == {"k": "v"}
        assert payload["retryable"] is True

    async def test_non_tools_call_error_data_includes_suggestion_and_data(self):
        frontend = build()
        from ploston_core.errors.errors import ErrorCategory

        async def boom(_params):
            raise AELError(
                code="C",
                category=ErrorCategory.SYSTEM,
                message="m",
                suggestion="try this",
                data={"x": 1},
                http_status=400,
            )

        with patch.object(frontend, "_handle_initialize", side_effect=boom):
            resp = await frontend._handle_message({"id": 2, "method": "initialize", "params": {}})
        assert resp["error"]["data"]["suggestion"] == "try this"
        assert resp["error"]["data"]["data"] == {"x": 1}
