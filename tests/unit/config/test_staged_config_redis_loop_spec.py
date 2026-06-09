"""Spec tests for StagedConfig Redis persistence under asyncio event loops (L-4).

L-4: ``_persist_to_redis`` / ``_clear_from_redis`` used ``asyncio.run()`` on the
no-running-loop path. ``asyncio.run()`` spins a *fresh* event loop per call, which
breaks when the redis client is bound to another loop ("got Future attached to a
different loop"). The fix mirrors the canonical running-loop pattern in
``ploston_core.runner_management.registry.ToolRegistry._fire_tools_changed``:
schedule on the already-running loop via ``create_task``; when no loop is running,
skip the fire-and-forget persistence instead of spinning a conflicting loop.
"""

from __future__ import annotations

import asyncio
import logging

from ploston_core.config.loader import ConfigLoader
from ploston_core.config.staged_config import StagedConfig


class _RunningLoopRedis:
    """Fake redis store whose async ops bind to whatever loop runs them."""

    def __init__(self) -> None:
        self.connected = True
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    async def set_value(self, key: str, value: str) -> bool:
        # Touch the running loop: a no-op await that only succeeds if the
        # coroutine runs on a live loop. Records the call for assertions.
        await asyncio.sleep(0)
        self.set_calls.append((key, value))
        return True

    async def delete_value(self, key: str) -> bool:
        await asyncio.sleep(0)
        self.delete_calls.append(key)
        return True


class _ForeignLoopRedis:
    """Fake redis client bound to a *separate* event loop.

    Mimics ``redis.asyncio`` created on one loop and then driven from another:
    awaiting its op from a different loop raises "got Future attached to a
    different loop". Reproduces the ``asyncio.run()`` no-loop-path defect.
    """

    def __init__(self) -> None:
        self.connected = True
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self._loop = asyncio.new_event_loop()
        self._fut = self._loop.create_future()
        self._loop.call_soon(self._fut.set_result, None)

    async def set_value(self, key: str, value: str) -> bool:
        # Awaiting a future owned by self._loop fails on any other loop.
        await self._fut
        self.set_calls.append((key, value))
        return True

    async def delete_value(self, key: str) -> bool:
        await self._fut
        self.delete_calls.append(key)
        return True

    def close(self) -> None:
        self._loop.close()


async def test_persist_on_running_loop_schedules_without_loop_conflict():
    """set() inside a running loop must persist via that loop (no asyncio.run)."""
    loader = ConfigLoader()
    redis = _RunningLoopRedis()
    staged = StagedConfig(loader, redis_store=redis)

    staged.set("server.port", 9000)

    # Let the scheduled create_task run to completion on this loop.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert redis.set_calls, "expected staged change to be persisted on running loop"
    key, _value = redis.set_calls[-1]
    assert key == "staged_config"


def test_persist_no_running_loop_does_not_spin_conflicting_loop(caplog):
    """Sync no-loop path must not break a foreign-loop-bound redis client.

    Before the fix this swallowed a "got Future attached to a different loop"
    error (logged as a warning) and dropped the write. After the fix the
    fire-and-forget persistence is skipped cleanly with no such warning.
    """
    loader = ConfigLoader()
    redis = _ForeignLoopRedis()
    try:
        staged = StagedConfig(loader, redis_store=redis)
        with caplog.at_level(logging.WARNING, logger="ploston_core.config.staged_config"):
            staged.set("server.port", 9000)

        conflict_warnings = [
            r.getMessage()
            for r in caplog.records
            if "different loop" in r.getMessage()
            or "cannot be called from a running event loop" in r.getMessage()
        ]
        assert not conflict_warnings, (
            f"no-loop persist path must not spin a conflicting event loop; got: {conflict_warnings}"
        )
    finally:
        redis.close()


def test_clear_no_running_loop_does_not_spin_conflicting_loop(caplog):
    """clear() on the sync no-loop path must not trip a loop conflict either."""
    loader = ConfigLoader()
    redis = _ForeignLoopRedis()
    try:
        staged = StagedConfig(loader, redis_store=redis)
        staged._changes = {"server": {"port": 9000}}
        with caplog.at_level(logging.WARNING, logger="ploston_core.config.staged_config"):
            staged.clear()

        conflict_warnings = [
            r.getMessage()
            for r in caplog.records
            if "different loop" in r.getMessage()
            or "cannot be called from a running event loop" in r.getMessage()
        ]
        assert not conflict_warnings, (
            f"no-loop clear path must not spin a conflicting event loop; got: {conflict_warnings}"
        )
        assert staged._changes == {}
    finally:
        redis.close()
