"""Robustness fixes for WorkflowRegistry (TDD).

Covers:
  #1 WORKFLOW_NOT_FOUND uses the right template kwarg so the name renders.
  #2 Fire-and-forget persistence tasks are retained (not GC-able) and their
     exceptions are logged, not silently swallowed.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from ploston_core.errors.errors import AELError
from ploston_core.workflow.registry import WorkflowRegistry


def _make_registry(logger=None):
    tool_registry = MagicMock()
    tool_registry.get.return_value = None
    config = MagicMock()
    config.directory = "/tmp/aaa-ploston-test-workflows"
    config.draft_ttl_seconds = 1800
    return WorkflowRegistry(
        tool_registry=tool_registry,
        config=config,
        logger=logger,
        redis_store=None,
    )


# --- #1 WORKFLOW_NOT_FOUND ----------------------------------------------------


def test_get_or_raise_renders_workflow_name_in_message():
    reg = _make_registry()
    with pytest.raises(AELError) as exc_info:
        reg.get_or_raise("my-missing-workflow")
    err = exc_info.value
    assert err.code == "WORKFLOW_NOT_FOUND"
    # The user-facing message must contain the actual name, not be blank.
    assert "my-missing-workflow" in err.message


# --- #2 fire-and-forget task retention + error logging ------------------------


@pytest.mark.asyncio
async def test_scheduled_task_is_retained_while_pending():
    reg = _make_registry()

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_coro():
        started.set()
        await release.wait()

    task = reg._schedule_background(slow_coro())
    await started.wait()

    # While pending the task must be held by a strong reference set so the GC
    # cannot collect it mid-flight.
    assert task in reg._background_tasks
    assert len(reg._background_tasks) >= 1

    release.set()
    await task

    # Done-callback should have discarded it from the retention set.
    assert task not in reg._background_tasks


@pytest.mark.asyncio
async def test_scheduled_task_exception_is_logged_not_swallowed():
    logger = MagicMock()
    reg = _make_registry(logger=logger)

    async def boom():
        raise RuntimeError("kaboom")

    task = reg._schedule_background(boom())
    # Awaiting the task surfaces the exception here, but the done-callback must
    # also log it (so true fire-and-forget callers are not blind to failures).
    with pytest.raises(RuntimeError):
        await task

    # Give the loop a tick for the done callback to run.
    await asyncio.sleep(0)

    assert logger._log.called, "background task failure must be logged"
    # The logged record should reference the error text.
    logged_args = [c.args for c in logger._log.call_args_list]
    assert any("kaboom" in str(a) for a in logged_args)
    assert task not in reg._background_tasks
