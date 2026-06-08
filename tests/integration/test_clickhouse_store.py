"""Integration tests for ClickHouseTelemetryStore (S-296 / T-945).

Spins up a real ClickHouse 24.8 container, runs migrations, then exercises
the full TelemetryStore CRUD surface against the live DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from ploston_core.telemetry.store.clickhouse import run_migrations
from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore
from ploston_core.telemetry.store.types import (
    ExecutionRecord,
    ExecutionStatus,
    ExecutionType,
    StepRecord,
    StepStatus,
    StepType,
    ToolCallRecord,
    ToolCallSource,
)

testcontainers = pytest.importorskip("testcontainers.clickhouse")
ClickHouseContainer = testcontainers.ClickHouseContainer

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest_asyncio.fixture(scope="module")
async def ch_store() -> Any:
    """Boot ClickHouse, run migrations, hand back a store."""
    container = ClickHouseContainer(
        image="clickhouse/clickhouse-server:24.8-alpine",
        username="test",
        password="test",
        dbname="default",
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(8123))

        await run_migrations(
            host=host,
            port=port,
            retention_days=7,
            username="test",
            password="test",
        )

        store = ClickHouseTelemetryStore(host=host, port=port, username="test", password="test")
        try:
            yield store
        finally:
            await store.close()
    finally:
        container.stop()


def _make_record(execution_id: str, started: datetime | None = None) -> ExecutionRecord:
    started = started or datetime.now(UTC)
    return ExecutionRecord(
        execution_id=execution_id,
        execution_type=ExecutionType.WORKFLOW,
        workflow_id="wf-1",
        status=ExecutionStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=1),
        duration_ms=1000,
        inputs={"k": "v"},
        outputs={"r": 1},
        source="mcp",
        session_id=f"sess-{execution_id}",
        runner_id="r-1",
        bridge_session_id="b-1",
        inputs_bytes=10,
        outputs_bytes=5,
        step_count=1,
        tool_call_count=1,
        total_response_bytes=42,
        steps=[
            StepRecord(
                step_id="s1",
                step_type=StepType.TOOL,
                status=StepStatus.COMPLETED,
                tool_name="t",
                started_at=started,
                completed_at=started + timedelta(milliseconds=500),
                duration_ms=500,
                tool_calls=[
                    ToolCallRecord(
                        call_id="c1",
                        tool_name="t",
                        started_at=started,
                        completed_at=started + timedelta(milliseconds=200),
                        duration_ms=200,
                        params={"a": 1},
                        result={"b": 2},
                        execution_id=execution_id,
                        step_id="s1",
                        source=ToolCallSource.DIRECT,
                        sequence=0,
                        params_bytes=11,
                        response_bytes=22,
                        runner_id="r-1",
                        bridge_id="b-1",
                        session_id=f"sess-{execution_id}",
                    )
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_save_and_get_round_trip(ch_store: ClickHouseTelemetryStore) -> None:
    rec = _make_record("exec-rt-1")
    await ch_store.save_execution(rec)
    out = await ch_store.get_execution("exec-rt-1")
    assert out is not None
    assert out.execution_id == "exec-rt-1"
    assert out.runner_id == "r-1"
    assert out.bridge_session_id == "b-1"
    assert out.session_id == "sess-exec-rt-1"
    assert len(out.steps) == 1
    assert len(out.steps[0].tool_calls) == 1
    call = out.steps[0].tool_calls[0]
    assert call.source == ToolCallSource.DIRECT
    assert call.params_bytes == 11
    assert call.response_bytes == 22
    assert call.bridge_id == "b-1"


@pytest.mark.asyncio
async def test_save_is_idempotent_via_delete_then_insert(
    ch_store: ClickHouseTelemetryStore,
) -> None:
    rec = _make_record("exec-idem")
    await ch_store.save_execution(rec)
    rec.duration_ms = 9999  # mutate and re-save
    await ch_store.save_execution(rec)
    out = await ch_store.get_execution("exec-idem")
    assert out is not None
    assert out.duration_ms == 9999
    # Only one row pair should exist
    assert len(out.steps) == 1
    assert len(out.steps[0].tool_calls) == 1


@pytest.mark.asyncio
async def test_get_missing_returns_none(ch_store: ClickHouseTelemetryStore) -> None:
    assert await ch_store.get_execution("never-existed") is None
