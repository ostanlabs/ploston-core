"""Spec tests for loop-safe workflow persistence (L-4 sibling; T-1114).

WorkflowRegistry.register_from_yaml / unregister persist workflows from sync
methods. The old no-running-loop branch called ``asyncio.run(self._persist(...))``,
which spins a fresh event loop and then awaits the Redis client there. The
Redis async client is bound to its creating loop, so that await raises
"attached to a different loop" — after the disk write — failing the whole
registration (mirrors L-4 in staged_config; DEC-229).

Fix: no running loop -> write the durable disk copy synchronously and skip the
best-effort Redis mirror. These assert the corrected contract:

* no loop + connected Redis -> disk written, Redis NOT touched, no raise;
* no loop + a Redis client that would raise on a foreign loop -> still no raise;
* running loop -> disk + Redis both written (unchanged).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from ploston_core.workflow.registry import WorkflowRegistry

SAMPLE_YAML = """\
name: loop-safe-wf
version: "1.0.0"
description: loop safety test
steps:
  - id: step1
    tool: echo
    mcp: system
    params:
      message: hello
"""


def _make_config(tmp_path: Path) -> MagicMock:
    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    return config


def _make_tool_registry() -> MagicMock:
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()
    tr.get.return_value = None
    echo_tool = MagicMock()
    echo_tool.name = "echo"
    echo_tool.server_name = "system"
    tr.list_tools.return_value = [echo_tool]
    return tr


def _make_redis_store(connected: bool = True) -> MagicMock:
    store = MagicMock()
    store.connected = connected
    store.set_value = AsyncMock(return_value=True)
    store.delete_value = AsyncMock(return_value=True)
    return store


def test_no_loop_writes_disk_and_skips_redis(tmp_path: Path):
    """No running loop + connected Redis: durable disk write, Redis skipped."""
    config = _make_config(tmp_path)
    redis_store = _make_redis_store(connected=True)
    registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

    # Plain synchronous call — no running event loop.
    registry.register_from_yaml(SAMPLE_YAML, persist=True)

    target = Path(config.directory) / "loop-safe-wf.yaml"
    assert target.exists(), "durable disk copy must be written even with no loop"
    assert target.read_text() == SAMPLE_YAML
    # Best-effort Redis mirror is skipped (its client is loop-bound).
    redis_store.set_value.assert_not_called()


def test_no_loop_does_not_raise_when_redis_would_fail_on_foreign_loop(tmp_path: Path):
    """The L-4 crash: a Redis client that errors on a foreign loop must not
    take down registration. With the fix Redis is skipped, so no raise."""
    config = _make_config(tmp_path)
    redis_store = _make_redis_store(connected=True)
    redis_store.set_value = AsyncMock(
        side_effect=RuntimeError("got Future attached to a different loop")
    )
    registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

    # Must not raise (pre-fix: asyncio.run awaited the failing client and raised).
    registry.register_from_yaml(SAMPLE_YAML, persist=True)

    assert (Path(config.directory) / "loop-safe-wf.yaml").exists()


def test_no_loop_unregister_removes_disk_and_skips_redis(tmp_path: Path):
    """unregister with no running loop removes the disk copy, skips Redis."""
    config = _make_config(tmp_path)
    redis_store = _make_redis_store(connected=True)
    registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

    registry.register_from_yaml(SAMPLE_YAML, persist=True)
    target = Path(config.directory) / "loop-safe-wf.yaml"
    assert target.exists()

    assert registry.unregister("loop-safe-wf") is True
    assert not target.exists(), "disk copy should be removed"
    redis_store.delete_value.assert_not_called()


def test_running_loop_writes_disk_and_redis(tmp_path: Path):
    """With a running loop the full disk+Redis persist still happens."""
    config = _make_config(tmp_path)
    redis_store = _make_redis_store(connected=True)
    registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

    loop = asyncio.new_event_loop()
    try:

        async def _run():
            registry.register_from_yaml(SAMPLE_YAML, persist=True)
            await asyncio.sleep(0.1)  # let the background persist task run

        loop.run_until_complete(_run())
    finally:
        loop.close()

    target = Path(config.directory) / "loop-safe-wf.yaml"
    assert target.exists()
    redis_store.set_value.assert_called_once_with("workflows:loop-safe-wf", SAMPLE_YAML)
