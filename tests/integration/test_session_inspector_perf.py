"""Session Inspector dashboard performance benchmark (S-299 acceptance).

Acceptance: Panel 3 (Tool Call Timeline) — the centerpiece query — must
return p95 < 1s for sessions containing 1000 tool calls.

The query mirrors the dashboard SQL but with `$session_id` and the
`$__timeFilter(started_at)` macro replaced by parameters.
"""

from __future__ import annotations

import time
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

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.docker]

# Matches dashboard Panel 3 with `$session_id` and time bounds substituted in.
# Uses clickhouse-connect's pyformat parameter style to match the store's
# existing query() conventions.
PANEL_3_SQL = """
SELECT
    started_at,
    if(execution_id != '', 'workflow', 'direct') AS kind,
    tool_name,
    duration_ms,
    params_bytes,
    response_bytes,
    runner_id,
    coalesce(if(error_code = '', NULL, error_code), 'ok') AS status,
    substring(params, 1, 200) AS params_preview,
    call_id
FROM ploston.tool_calls
WHERE session_id = %(session_id)s
  AND started_at BETWEEN %(since)s AND %(until)s
ORDER BY started_at ASC
"""


@pytest_asyncio.fixture(scope="module")
async def seeded_store() -> Any:
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
            host=host, port=port, retention_days=7, username="test", password="test"
        )
        store = ClickHouseTelemetryStore(host=host, port=port, username="test", password="test")
        # Seed a single session with 1000 tool calls grouped under 10 executions.
        base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
        sid = "perf-session-1"
        for ex in range(10):
            tool_calls: list[ToolCallRecord] = []
            for i in range(100):
                offset = ex * 100 + i
                ts = base + timedelta(milliseconds=offset * 10)
                tool_calls.append(
                    ToolCallRecord(
                        call_id=f"c-{ex:02d}-{i:03d}",
                        tool_name=f"tool_{i % 5}",
                        started_at=ts,
                        completed_at=ts + timedelta(milliseconds=2),
                        duration_ms=2,
                        execution_id=f"exec-{ex}",
                        step_id="s1",
                        source=ToolCallSource.TOOL_STEP,
                        sequence=i,
                        session_id=sid,
                    )
                )
            await store.save_execution(
                ExecutionRecord(
                    execution_id=f"exec-{ex}",
                    execution_type=ExecutionType.WORKFLOW,
                    workflow_id="wf-perf",
                    status=ExecutionStatus.COMPLETED,
                    started_at=base + timedelta(seconds=ex),
                    completed_at=base + timedelta(seconds=ex + 1),
                    duration_ms=1000,
                    source="mcp",
                    session_id=sid,
                    steps=[
                        StepRecord(
                            step_id="s1",
                            step_type=StepType.TOOL,
                            status=StepStatus.COMPLETED,
                            tool_name="tool_0",
                            started_at=base + timedelta(seconds=ex),
                            completed_at=base + timedelta(seconds=ex, milliseconds=10),
                            duration_ms=10,
                            tool_calls=tool_calls,
                        )
                    ],
                )
            )
        try:
            yield store, sid, base
        finally:
            await store.close()
    finally:
        container.stop()


@pytest.mark.asyncio
async def test_session_inspector_centerpiece_p95_under_1s(
    seeded_store: tuple[ClickHouseTelemetryStore, str, datetime],
) -> None:
    store, sid, base = seeded_store
    since = base - timedelta(seconds=1)
    until = base + timedelta(hours=1)

    client = await store._ensure_client()
    timings: list[float] = []
    iterations = 30  # cap CI cost; 30 samples → p95 = 28th-ranked
    for _ in range(iterations):
        t0 = time.perf_counter()
        result = await client.query(
            PANEL_3_SQL,
            parameters={"session_id": sid, "since": since, "until": until},
        )
        rows = list(result.named_results())
        timings.append(time.perf_counter() - t0)
        assert len(rows) == 1000

    timings.sort()
    p95_index = max(0, int(len(timings) * 0.95) - 1)
    p95 = timings[p95_index]
    assert p95 < 1.0, f"p95 query latency {p95:.3f}s exceeds 1s budget"
