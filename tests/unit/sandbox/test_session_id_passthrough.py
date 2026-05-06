"""S-304/M-082 — verify CODE_BLOCK tool calls record bridge_id & session_id.

Regression: ``ToolCallInterface._invoke_tracked`` previously dropped
bridge identity on the floor, leaving every CODE_BLOCK ``tool_calls``
row with NULL ``bridge_id`` / ``session_id``. RunnerContext now carries
both, and they must flow into ``record_tool_call``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.sandbox.types import RunnerContext, ToolCallInterface


@pytest.mark.asyncio
async def test_invoke_tracked_passes_bridge_and_session_ids() -> None:
    rc = RunnerContext(
        runner_name="laptop",
        step_id="step-1",
        execution_id="exec-1",
        bridge_id="bridge-A",
        session_id="conv-42",
    )
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"ok": True})
    collector = MagicMock()
    iface = ToolCallInterface(
        tool_caller=caller,
        max_calls=10,
        runner_context=rc,
        telemetry_collector=collector,
    )

    captured: dict[str, object] = {}

    class _DummyCM:
        async def __aenter__(self):
            return MagicMock(set_result=MagicMock(), set_error=MagicMock())

        async def __aexit__(self, *exc):
            return False

    def _capture(_collector, **kwargs):
        captured.update(kwargs)
        return _DummyCM()

    with patch("ploston_core.telemetry.store.record_tool_call", side_effect=_capture):
        await iface._invoke_tracked(
            invoke_name="laptop__obsidian__list_files",
            display_tool="obsidian__list_files",
            params={"path": "/x"},
            runner_id="laptop",
        )

    assert captured["bridge_id"] == "bridge-A"
    assert captured["session_id"] == "conv-42"
    assert captured["runner_id"] == "laptop"
    assert captured["execution_id"] == "exec-1"


@pytest.mark.asyncio
async def test_invoke_tracked_session_id_none_when_runner_ctx_lacks_them() -> None:
    rc = RunnerContext(step_id="step-1", execution_id="exec-1")
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"ok": True})
    collector = MagicMock()
    iface = ToolCallInterface(
        tool_caller=caller,
        max_calls=10,
        runner_context=rc,
        telemetry_collector=collector,
    )

    captured: dict[str, object] = {}

    class _DummyCM:
        async def __aenter__(self):
            return MagicMock(set_result=MagicMock(), set_error=MagicMock())

        async def __aexit__(self, *exc):
            return False

    def _capture(_collector, **kwargs):
        captured.update(kwargs)
        return _DummyCM()

    with patch("ploston_core.telemetry.store.record_tool_call", side_effect=_capture):
        await iface._invoke_tracked(invoke_name="x", display_tool="x", params={}, runner_id=None)

    assert captured["bridge_id"] is None
    assert captured["session_id"] is None
