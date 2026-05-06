"""Tests for S-304 record_tool_call / synthetic_direct_step helpers."""

from __future__ import annotations

import pytest

from ploston_core.telemetry.store import (
    ErrorRecord,
    ExecutionStatus,
    ExecutionType,
    StepStatus,
    ToolCallSource,
    record_tool_call,
    synthetic_direct_step,
)
from ploston_core.telemetry.store.collector import TelemetryCollector
from ploston_core.telemetry.store.config import TelemetryStoreConfig
from ploston_core.telemetry.store.memory import MemoryTelemetryStore


@pytest.fixture
def store() -> MemoryTelemetryStore:
    return MemoryTelemetryStore(max_records=100)


@pytest.fixture
def collector(store: MemoryTelemetryStore) -> TelemetryCollector:
    return TelemetryCollector(store=store, config=TelemetryStoreConfig(enabled=True))


class TestRecordToolCall:
    @pytest.mark.asyncio
    async def test_records_success(
        self,
        collector: TelemetryCollector,
        store: MemoryTelemetryStore,
    ) -> None:
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.WORKFLOW, workflow_id="wf-1"
        )
        async with synthetic_direct_step(
            collector, execution_id=exec_id, tool_name="search_web"
        ) as step_id:
            async with record_tool_call(
                collector,
                execution_id=exec_id,
                step_id=step_id,
                tool_name="search_web",
                params={"q": "ploston"},
                source=ToolCallSource.DIRECT,
                runner_id="rid",
                bridge_id="bid",
                session_id="sid",
            ) as handle:
                handle.set_result({"hits": 3})

        await collector.end_execution(exec_id, status=ExecutionStatus.COMPLETED)
        record = await store.get_execution(exec_id)
        assert record is not None
        assert len(record.steps) == 1
        call = record.steps[0].tool_calls[0]
        assert call.tool_name == "search_web"
        assert call.source == ToolCallSource.DIRECT
        assert call.runner_id == "rid"
        assert call.bridge_id == "bid"
        assert call.session_id == "sid"
        assert call.params_bytes > 0
        assert call.response_bytes > 0
        assert call.error is None

    @pytest.mark.asyncio
    async def test_records_error(
        self,
        collector: TelemetryCollector,
        store: MemoryTelemetryStore,
    ) -> None:
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.DIRECT, workflow_id=None
        )
        async with synthetic_direct_step(
            collector, execution_id=exec_id, tool_name="bad_tool"
        ) as step_id:
            async with record_tool_call(
                collector,
                execution_id=exec_id,
                step_id=step_id,
                tool_name="bad_tool",
                params={},
                source=ToolCallSource.DIRECT,
            ) as handle:
                handle.set_error(ErrorRecord(code="X", category="tool", message="boom"))

        await collector.end_execution(exec_id, status=ExecutionStatus.FAILED)
        rec = await store.get_execution(exec_id)
        assert rec is not None
        call = rec.steps[0].tool_calls[0]
        assert call.error is not None
        assert call.error.code == "X"

    @pytest.mark.asyncio
    async def test_noop_when_collector_none(self) -> None:
        async with record_tool_call(
            None,
            execution_id="any",
            step_id="direct",
            tool_name="x",
            params=None,
            source=ToolCallSource.DIRECT,
        ) as handle:
            handle.set_result({"ok": True})
        # no exception, no record — pass

    @pytest.mark.asyncio
    async def test_noop_when_execution_id_none(self, collector: TelemetryCollector) -> None:
        async with record_tool_call(
            collector,
            execution_id=None,
            step_id="direct",
            tool_name="x",
            params=None,
            source=ToolCallSource.DIRECT,
        ) as handle:
            handle.set_result({"ok": True})

    @pytest.mark.asyncio
    async def test_swallows_collector_failure(
        self, collector: TelemetryCollector, monkeypatch
    ) -> None:
        async def boom(*args, **kwargs):
            raise RuntimeError("clickhouse down")

        monkeypatch.setattr(collector, "start_tool_call", boom)
        # User code must not see this fail.
        async with record_tool_call(
            collector,
            execution_id="ex1",
            step_id="direct",
            tool_name="x",
            params={},
            source=ToolCallSource.DIRECT,
        ) as handle:
            handle.set_result({"ok": True})


class TestSyntheticDirectStep:
    @pytest.mark.asyncio
    async def test_step_completed_on_success(
        self,
        collector: TelemetryCollector,
        store: MemoryTelemetryStore,
    ) -> None:
        exec_id = await collector.start_execution(execution_type=ExecutionType.DIRECT)
        async with synthetic_direct_step(collector, execution_id=exec_id, tool_name="t"):
            pass
        await collector.end_execution(exec_id, status=ExecutionStatus.COMPLETED)
        rec = await store.get_execution(exec_id)
        assert rec is not None
        assert rec.steps[0].step_id == "direct"
        assert rec.steps[0].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_step_failed_on_exception(
        self,
        collector: TelemetryCollector,
        store: MemoryTelemetryStore,
    ) -> None:
        exec_id = await collector.start_execution(execution_type=ExecutionType.DIRECT)
        with pytest.raises(ValueError):
            async with synthetic_direct_step(collector, execution_id=exec_id, tool_name="t"):
                raise ValueError("oops")
        await collector.end_execution(exec_id, status=ExecutionStatus.FAILED)
        rec = await store.get_execution(exec_id)
        assert rec is not None
        assert rec.steps[0].status == StepStatus.FAILED
