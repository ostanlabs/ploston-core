"""Unit tests for ClickHouseTelemetryStore — no live DB required.

Covers:
- T-967: extended record types round-trip through serialize/deserialize.
- T-942: factory wiring + config validation.
- T-944: __post_init__ validation rejects empty host.
- T-943: store-level CRUD with a mocked clickhouse_connect AsyncClient.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.telemetry.store.base import create_telemetry_store
from ploston_core.telemetry.store.clickhouse.serialization import (
    EXECUTIONS_COLS,
    STEPS_COLS,
    TOOL_CALLS_COLS,
    assemble_execution,
    deserialize_execution,
    deserialize_step,
    deserialize_tool_call,
    serialize_execution,
    serialize_step,
    serialize_tool_call,
)
from ploston_core.telemetry.store.config import TelemetryStoreConfig
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


def _sample_record() -> ExecutionRecord:
    now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
    return ExecutionRecord(
        execution_id="exec-1",
        execution_type=ExecutionType.WORKFLOW,
        workflow_id="wf",
        status=ExecutionStatus.COMPLETED,
        started_at=now,
        completed_at=now,
        duration_ms=10,
        inputs={"k": "v"},
        outputs={"r": 1},
        source="mcp",
        session_id="sess-1",
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
                started_at=now,
                completed_at=now,
                duration_ms=5,
                tool_calls=[
                    ToolCallRecord(
                        call_id="c1",
                        tool_name="t",
                        started_at=now,
                        completed_at=now,
                        duration_ms=3,
                        params={"a": 1},
                        result={"b": 2},
                        execution_id="exec-1",
                        step_id="s1",
                        source=ToolCallSource.DIRECT,
                        sequence=0,
                        params_bytes=11,
                        response_bytes=22,
                        runner_id="r-1",
                        bridge_id="b-1",
                        session_id="sess-1",
                    )
                ],
            )
        ],
    )


# ─────────────────────────────────────────────────────────────────
# T-967 — record extensions
# ─────────────────────────────────────────────────────────────────


def test_tool_call_source_direct_value() -> None:
    assert ToolCallSource.DIRECT.value == "direct"


def test_serialize_execution_columns_match_ddl() -> None:
    rec = _sample_record()
    row = serialize_execution(rec, redactor=None)
    assert len(row) == len(EXECUTIONS_COLS)


def test_serialize_step_columns_match_ddl() -> None:
    rec = _sample_record()
    row = serialize_step(rec.execution_id, rec.steps[0], redactor=None)
    assert len(row) == len(STEPS_COLS)


def test_serialize_tool_call_columns_match_ddl() -> None:
    rec = _sample_record()
    row = serialize_tool_call(rec.steps[0].tool_calls[0], redactor=None)
    assert len(row) == len(TOOL_CALLS_COLS)


def _row_dict(cols: list[str], row: list[Any]) -> dict[str, Any]:
    return dict(zip(cols, row, strict=True))


def test_round_trip_execution() -> None:
    rec = _sample_record()
    exec_row = _row_dict(EXECUTIONS_COLS, serialize_execution(rec, None))
    out = deserialize_execution(exec_row)
    assert out.execution_id == rec.execution_id
    assert out.runner_id == rec.runner_id
    assert out.bridge_session_id == rec.bridge_session_id
    assert out.session_id == rec.session_id
    assert out.inputs_bytes == rec.inputs_bytes
    assert out.outputs_bytes == rec.outputs_bytes
    assert out.step_count == rec.step_count


def test_round_trip_tool_call_extended_fields() -> None:
    rec = _sample_record()
    call = rec.steps[0].tool_calls[0]
    row = _row_dict(TOOL_CALLS_COLS, serialize_tool_call(call, None))
    out = deserialize_tool_call(row)
    assert out.params_bytes == 11
    assert out.response_bytes == 22
    assert out.runner_id == "r-1"
    assert out.bridge_id == "b-1"
    assert out.session_id == "sess-1"
    assert out.source == ToolCallSource.DIRECT


def test_assemble_execution_attaches_calls_to_steps() -> None:
    rec = _sample_record()
    exec_row = _row_dict(EXECUTIONS_COLS, serialize_execution(rec, None))
    step_row = _row_dict(STEPS_COLS, serialize_step(rec.execution_id, rec.steps[0], None))
    call_row = _row_dict(TOOL_CALLS_COLS, serialize_tool_call(rec.steps[0].tool_calls[0], None))
    out = assemble_execution(exec_row, [step_row], [call_row])
    assert len(out.steps) == 1
    assert len(out.steps[0].tool_calls) == 1
    assert out.steps[0].tool_calls[0].call_id == "c1"


def test_deserialize_step_minimal() -> None:
    row = {
        "execution_id": "e",
        "step_id": "s",
        "step_type": "tool",
        "status": "completed",
        "attempt": 1,
        "max_attempts": 1,
    }
    out = deserialize_step(row)
    assert out.step_id == "s"
    assert out.attempt == 1


# ─────────────────────────────────────────────────────────────────
# T-942 / T-944 — factory + config
# ─────────────────────────────────────────────────────────────────


def test_config_validates_clickhouse_host() -> None:
    with pytest.raises(ValueError, match="clickhouse_host"):
        TelemetryStoreConfig(storage_type="clickhouse", clickhouse_host="")


def test_factory_returns_clickhouse_store_class() -> None:
    cfg = TelemetryStoreConfig(
        storage_type="clickhouse",
        clickhouse_host="ch.local",
        clickhouse_port=8123,
    )
    store = create_telemetry_store(cfg)
    from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore

    assert isinstance(store, ClickHouseTelemetryStore)
    assert store._host == "ch.local"
    assert store._database == "ploston"


# ─────────────────────────────────────────────────────────────────
# T-943 — CRUD against a mocked clickhouse_connect client
# ─────────────────────────────────────────────────────────────────


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.command = AsyncMock()
    client.insert = AsyncMock()
    client.query = AsyncMock()
    client.close = AsyncMock()
    return client


def _query_result(rows: list[dict[str, Any]]) -> MagicMock:
    qr = MagicMock()
    qr.named_results = MagicMock(return_value=iter(rows))
    return qr


@pytest.mark.asyncio
async def test_save_execution_uses_three_inserts_and_three_deletes() -> None:
    from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore

    store = ClickHouseTelemetryStore(host="x")
    mock = _make_mock_client()
    store._client = mock  # type: ignore[assignment]

    await store.save_execution(_sample_record())

    assert mock.command.await_count == 3  # 3 deletes (children → parent)
    assert mock.insert.await_count == 3  # executions, steps, tool_calls
    insert_targets = [call.args[0] for call in mock.insert.await_args_list]
    assert insert_targets == ["executions", "steps", "tool_calls"]


@pytest.mark.asyncio
async def test_get_execution_returns_none_when_missing() -> None:
    from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore

    store = ClickHouseTelemetryStore(host="x")
    mock = _make_mock_client()
    mock.query.return_value = _query_result([])
    store._client = mock  # type: ignore[assignment]

    assert await store.get_execution("nope") is None


@pytest.mark.asyncio
async def test_delete_execution_returns_false_when_missing() -> None:
    from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore

    store = ClickHouseTelemetryStore(host="x")
    mock = _make_mock_client()
    mock.query.return_value = _query_result([{"c": 0}])
    store._client = mock  # type: ignore[assignment]

    assert await store.delete_execution("nope") is False
    assert mock.command.await_count == 0


@pytest.mark.asyncio
async def test_close_resets_client() -> None:
    from ploston_core.telemetry.store.clickhouse.store import ClickHouseTelemetryStore

    store = ClickHouseTelemetryStore(host="x")
    store._client = _make_mock_client()  # type: ignore[assignment]
    await store.close()
    assert store._client is None
