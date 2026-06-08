"""Spec tests for the API execution store layer.

Focuses on the ``SQLiteExecutionStore`` (round-trip persistence, filtering,
pagination, ordering, log retrieval) and the in-memory store's remaining
filter/log paths. Both implement the ``ExecutionStore`` ABC contract, so the
same behavioural expectations apply.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ploston_core.api.models import (
    ErrorDetail,
    ExecutionDetail,
    ExecutionStatus,
    StepSummary,
)
from ploston_core.api.store import InMemoryExecutionStore, SQLiteExecutionStore


def _execution(
    execution_id: str,
    *,
    workflow_id: str = "wf",
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    started_at: datetime | None = None,
    error: ErrorDetail | None = None,
    with_steps: bool = True,
    step_timing: bool = False,
) -> ExecutionDetail:
    """Build an ExecutionDetail.

    ``step_timing`` controls whether step rows carry ``started_at``/
    ``completed_at`` datetimes. The SQLite store serializes steps with
    ``model_dump()`` (native datetimes), so timing-bearing steps exercise
    the JSON-encode path; see ``TestSQLiteStepSerializationBug``.
    """
    now = started_at or datetime.now(UTC)
    steps = (
        [
            StepSummary(
                id="step-1",
                tool="tool_a",
                type="tool",
                status=status,
                started_at=now if step_timing else None,
                completed_at=now if step_timing else None,
                duration_ms=10,
            )
        ]
        if with_steps
        else []
    )
    return ExecutionDetail(
        execution_id=execution_id,
        workflow_id=workflow_id,
        status=status,
        inputs={"k": "v"},
        outputs={"out": 1},
        started_at=now,
        completed_at=now if status == ExecutionStatus.COMPLETED else None,
        duration_ms=100 if status == ExecutionStatus.COMPLETED else None,
        error=error,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# SQLiteExecutionStore
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_store(tmp_path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(str(tmp_path / "exec.db"))


class TestSQLiteSaveGet:
    @pytest.mark.asyncio
    async def test_save_and_get_round_trips_all_fields(
        self, sqlite_store: SQLiteExecutionStore
    ) -> None:
        execution = _execution("exec-1")
        await sqlite_store.save(execution)

        got = await sqlite_store.get("exec-1")
        assert got is not None
        assert got.execution_id == "exec-1"
        assert got.workflow_id == "wf"
        assert got.status == ExecutionStatus.COMPLETED
        assert got.inputs == {"k": "v"}
        assert got.outputs == {"out": 1}
        assert got.duration_ms == 100
        assert len(got.steps) == 1
        assert got.steps[0].id == "step-1"
        assert got.steps[0].tool == "tool_a"

    @pytest.mark.asyncio
    async def test_save_round_trips_steps_with_timing(
        self, sqlite_store: SQLiteExecutionStore
    ) -> None:
        """CONTRACT (per docstring "Save an execution record"): a normal
        engine-produced execution — whose steps carry ``started_at`` /
        ``completed_at`` datetimes — must persist and reload intact.

        EXPECTED RED: ``save`` serializes steps via ``model_dump()`` (native
        datetimes) then ``json.dumps``, which raises
        ``TypeError: Object of type datetime is not JSON serializable``.
        See the bug report; the fix is ``model_dump(mode="json")``.
        """
        execution = _execution("exec-timed", step_timing=True)
        await sqlite_store.save(execution)

        got = await sqlite_store.get("exec-timed")
        assert got is not None
        assert got.steps[0].started_at is not None
        assert got.steps[0].completed_at is not None

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, sqlite_store: SQLiteExecutionStore) -> None:
        assert await sqlite_store.get("missing") is None

    @pytest.mark.asyncio
    async def test_save_is_upsert(self, sqlite_store: SQLiteExecutionStore) -> None:
        await sqlite_store.save(_execution("exec-1", status=ExecutionStatus.RUNNING))
        await sqlite_store.save(_execution("exec-1", status=ExecutionStatus.COMPLETED))

        got = await sqlite_store.get("exec-1")
        assert got is not None
        assert got.status == ExecutionStatus.COMPLETED
        # Still a single row.
        _, total = await sqlite_store.list()
        assert total == 1

    @pytest.mark.asyncio
    async def test_round_trips_error_detail(self, sqlite_store: SQLiteExecutionStore) -> None:
        err = ErrorDetail(code="BOOM", category="tool", message="exploded")
        await sqlite_store.save(_execution("exec-err", status=ExecutionStatus.FAILED, error=err))

        got = await sqlite_store.get("exec-err")
        assert got is not None
        assert got.error is not None
        assert got.error.code == "BOOM"
        assert got.error.message == "exploded"

    @pytest.mark.asyncio
    async def test_pending_execution_has_no_completed_at(
        self, sqlite_store: SQLiteExecutionStore
    ) -> None:
        await sqlite_store.save(
            _execution("exec-run", status=ExecutionStatus.RUNNING, with_steps=False)
        )

        got = await sqlite_store.get("exec-run")
        assert got is not None
        assert got.completed_at is None
        assert got.duration_ms is None
        assert got.steps == []

    @pytest.mark.asyncio
    async def test_persists_across_store_instances(self, tmp_path) -> None:
        db = str(tmp_path / "persist.db")
        store1 = SQLiteExecutionStore(db)
        await store1.save(_execution("durable"))

        store2 = SQLiteExecutionStore(db)
        got = await store2.get("durable")
        assert got is not None
        assert got.execution_id == "durable"


class TestSQLiteList:
    @pytest.mark.asyncio
    async def test_list_all(self, sqlite_store: SQLiteExecutionStore) -> None:
        for i in range(3):
            await sqlite_store.save(_execution(f"e-{i}"))

        rows, total = await sqlite_store.list()
        assert total == 3
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_filter_by_workflow(self, sqlite_store: SQLiteExecutionStore) -> None:
        await sqlite_store.save(_execution("e1", workflow_id="a"))
        await sqlite_store.save(_execution("e2", workflow_id="b"))
        await sqlite_store.save(_execution("e3", workflow_id="a"))

        rows, total = await sqlite_store.list(workflow_id="a")
        assert total == 2
        assert all(r.workflow_id == "a" for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_status(self, sqlite_store: SQLiteExecutionStore) -> None:
        await sqlite_store.save(_execution("e1", status=ExecutionStatus.COMPLETED))
        await sqlite_store.save(_execution("e2", status=ExecutionStatus.FAILED, with_steps=False))

        rows, total = await sqlite_store.list(status=ExecutionStatus.FAILED)
        assert total == 1
        assert rows[0].status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_filter_by_since_and_until(self, sqlite_store: SQLiteExecutionStore) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        await sqlite_store.save(_execution("old", started_at=base))
        await sqlite_store.save(_execution("mid", started_at=base + timedelta(days=5)))
        await sqlite_store.save(_execution("new", started_at=base + timedelta(days=10)))

        rows, total = await sqlite_store.list(
            since=base + timedelta(days=1), until=base + timedelta(days=6)
        )
        assert total == 1
        assert rows[0].execution_id == "mid"

    @pytest.mark.asyncio
    async def test_list_ordered_by_started_at_desc(
        self, sqlite_store: SQLiteExecutionStore
    ) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        await sqlite_store.save(_execution("first", started_at=base))
        await sqlite_store.save(_execution("second", started_at=base + timedelta(hours=1)))
        await sqlite_store.save(_execution("third", started_at=base + timedelta(hours=2)))

        rows, _ = await sqlite_store.list()
        assert [r.execution_id for r in rows] == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_pagination(self, sqlite_store: SQLiteExecutionStore) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            await sqlite_store.save(_execution(f"e-{i}", started_at=base + timedelta(minutes=i)))

        page1, total = await sqlite_store.list(page=1, page_size=2)
        page2, _ = await sqlite_store.list(page=2, page_size=2)
        page3, _ = await sqlite_store.list(page=3, page_size=2)

        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
        ids = {r.execution_id for r in page1 + page2 + page3}
        assert len(ids) == 5  # no overlap, full coverage

    @pytest.mark.asyncio
    async def test_empty_list(self, sqlite_store: SQLiteExecutionStore) -> None:
        rows, total = await sqlite_store.list()
        assert rows == []
        assert total == 0


class TestSQLiteLogs:
    @pytest.mark.asyncio
    async def test_get_logs_empty(self, sqlite_store: SQLiteExecutionStore) -> None:
        await sqlite_store.save(_execution("e1"))
        assert await sqlite_store.get_logs("e1") == []

    @pytest.mark.asyncio
    async def test_get_logs_returns_inserted_rows_and_filters(
        self, sqlite_store: SQLiteExecutionStore
    ) -> None:
        await sqlite_store.save(_execution("e1"))
        # Insert log rows directly via the same schema the store created.
        import sqlite3

        with sqlite3.connect(sqlite_store.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO execution_logs
                (execution_id, timestamp, level, component, step_id, tool_name, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("e1", "2026-01-01T00:00:00", "INFO", "engine", "s1", "t", "a"),
                    ("e1", "2026-01-01T00:00:01", "ERROR", "engine", "s2", "t", "b"),
                    ("e1", "2026-01-01T00:00:02", "INFO", "engine", "s1", "t", "c"),
                ],
            )

        all_logs = await sqlite_store.get_logs("e1")
        assert len(all_logs) == 3

        errors = await sqlite_store.get_logs("e1", level="ERROR")
        assert len(errors) == 1
        assert errors[0]["message"] == "b"

        step1 = await sqlite_store.get_logs("e1", step_id="s1")
        assert len(step1) == 2
        assert {row["message"] for row in step1} == {"a", "c"}

    @pytest.mark.asyncio
    async def test_get_logs_combined_filters(self, sqlite_store: SQLiteExecutionStore) -> None:
        await sqlite_store.save(_execution("e1"))
        import sqlite3

        with sqlite3.connect(sqlite_store.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO execution_logs
                (execution_id, timestamp, level, component, step_id, tool_name, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("e1", "t1", "ERROR", "c", "s1", "t", "match"),
                    ("e1", "t2", "ERROR", "c", "s2", "t", "other-step"),
                    ("e1", "t3", "INFO", "c", "s1", "t", "other-level"),
                ],
            )

        rows = await sqlite_store.get_logs("e1", level="ERROR", step_id="s1")
        assert len(rows) == 1
        assert rows[0]["message"] == "match"


# ---------------------------------------------------------------------------
# InMemoryExecutionStore — remaining filter/log paths
# ---------------------------------------------------------------------------


class TestInMemoryRemaining:
    @pytest.mark.asyncio
    async def test_filter_by_since_and_until(self) -> None:
        store = InMemoryExecutionStore(max_records=10)
        base = datetime(2026, 1, 1, tzinfo=UTC)
        await store.save(_execution("old", started_at=base))
        await store.save(_execution("mid", started_at=base + timedelta(days=5)))
        await store.save(_execution("new", started_at=base + timedelta(days=10)))

        rows, total = await store.list(
            since=base + timedelta(days=1), until=base + timedelta(days=6)
        )
        assert total == 1
        assert rows[0].execution_id == "mid"

    @pytest.mark.asyncio
    async def test_list_sorted_desc(self) -> None:
        store = InMemoryExecutionStore(max_records=10)
        base = datetime(2026, 1, 1, tzinfo=UTC)
        await store.save(_execution("a", started_at=base))
        await store.save(_execution("b", started_at=base + timedelta(hours=1)))

        rows, _ = await store.list()
        assert [r.execution_id for r in rows] == ["b", "a"]

    @pytest.mark.asyncio
    async def test_add_log_and_filter(self) -> None:
        store = InMemoryExecutionStore(max_records=10)
        await store.save(_execution("e1"))
        await store.add_log("e1", {"level": "INFO", "step_id": "s1", "message": "a"})
        await store.add_log("e1", {"level": "ERROR", "step_id": "s2", "message": "b"})

        assert len(await store.get_logs("e1")) == 2
        errs = await store.get_logs("e1", level="ERROR")
        assert len(errs) == 1 and errs[0]["message"] == "b"
        s1 = await store.get_logs("e1", step_id="s1")
        assert len(s1) == 1 and s1[0]["message"] == "a"

    @pytest.mark.asyncio
    async def test_lru_eviction_drops_logs_of_evicted_execution(self) -> None:
        store = InMemoryExecutionStore(max_records=2)
        await store.save(_execution("e1"))
        await store.add_log("e1", {"level": "INFO", "message": "x"})
        await store.save(_execution("e2"))
        await store.save(_execution("e3"))  # evicts e1 (oldest)

        assert await store.get("e1") is None
        # Logs for the evicted execution must be cleaned up too.
        assert await store.get_logs("e1") == []
