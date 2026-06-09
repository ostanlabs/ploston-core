"""Spec tests for execution-status boundary mapping (R-6 siblings).

Wave 2 fixed workflows.py; these cover the two sibling sites flagged at the
time (tracked as T-1112):

* ``executions.py`` list endpoint: filtering by a step-only API status (e.g.
  ``skipped``) must NOT 500 when the telemetry execution-status enum has no such
  member — it should return an empty page.
* ``execution_adapter.py``: telemetry ``StepStatus``/``ExecutionStatus`` ->
  API ``ExecutionStatus`` must never raise at the response boundary; unknown
  values degrade to ``PENDING``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.models.execution import ExecutionStatus
from ploston_core.api.routers.execution_adapter import _to_step_summary, to_execution_summary
from ploston_core.api.routers.executions import execution_router
from ploston_core.telemetry.store.types import (
    ExecutionRecord,
    ExecutionType,
    StepRecord,
    StepStatus,
    StepType,
)
from ploston_core.telemetry.store.types import ExecutionStatus as TelemetryExecutionStatus


class _FakeStore:
    """Minimal telemetry store stub recording the status it was queried with."""

    def __init__(self) -> None:
        self.queried_status: object = "UNSET"

    async def list_executions(self, *, status=None, **_kwargs):
        self.queried_status = status
        return ([], 0)


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


@pytest.fixture
def client(store: _FakeStore) -> TestClient:
    app = FastAPI()
    app.include_router(execution_router, prefix="/api/v1")
    app.state.telemetry_store = store
    return TestClient(app)


class TestListExecutionsStatusFilter:
    def test_skipped_status_returns_empty_not_500(self, client: TestClient, store: _FakeStore):
        """?status=skipped (no telemetry equivalent) -> 200 empty, never 500."""
        resp = client.get("/api/v1/executions", params={"status": "skipped"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["executions"] == []
        assert body["total"] == 0
        # Short-circuited before hitting the store (no equivalent telemetry status).
        assert store.queried_status == "UNSET"

    def test_completed_status_maps_and_queries_store(self, client: TestClient, store: _FakeStore):
        """A status with a telemetry equivalent is mapped and passed through."""
        resp = client.get("/api/v1/executions", params={"status": "completed"})
        assert resp.status_code == 200
        assert store.queried_status == TelemetryExecutionStatus.COMPLETED

    def test_no_status_filter_passes_none(self, client: TestClient, store: _FakeStore):
        resp = client.get("/api/v1/executions")
        assert resp.status_code == 200
        assert store.queried_status is None


class TestAdapterStatusMapping:
    def test_skipped_step_maps_to_skipped(self):
        step = StepRecord(step_id="s1", step_type=StepType.TOOL, status=StepStatus.SKIPPED)
        assert _to_step_summary(step).status == ExecutionStatus.SKIPPED

    def test_each_step_status_round_trips_without_raising(self):
        for st in StepStatus:
            step = StepRecord(step_id="s", step_type=StepType.TOOL, status=st)
            # Must not raise; value preserved when the API enum has the member.
            assert isinstance(_to_step_summary(step).status, ExecutionStatus)

    def test_each_execution_status_round_trips(self):
        for st in TelemetryExecutionStatus:
            rec = ExecutionRecord(
                execution_id="e",
                execution_type=ExecutionType.WORKFLOW,
                status=st,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            assert to_execution_summary(rec).status == ExecutionStatus(st.value)


class TestFromValueSafe:
    def test_known_value_preserved(self):
        assert ExecutionStatus.from_value_safe("skipped") == ExecutionStatus.SKIPPED

    def test_unknown_value_degrades_to_pending(self):
        assert ExecutionStatus.from_value_safe("totally-bogus") == ExecutionStatus.PENDING
