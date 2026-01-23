"""Execution store for persisting execution records."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime
from typing import Any

from ploston_core.api.models import ExecutionDetail, ExecutionStatus


class ExecutionStore(ABC):
    """Abstract base class for execution storage."""

    @abstractmethod
    async def save(self, execution: ExecutionDetail) -> None:
        """Save an execution record."""

    @abstractmethod
    async def get(self, execution_id: str) -> ExecutionDetail | None:
        """Get an execution by ID."""

    @abstractmethod
    async def list(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExecutionDetail], int]:
        """List executions with filtering."""

    @abstractmethod
    async def get_logs(
        self,
        execution_id: str,
        level: str | None = None,
        step_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get logs for an execution."""


class InMemoryExecutionStore(ExecutionStore):
    """In-memory execution store with LRU eviction."""

    def __init__(self, max_records: int = 1000):
        """Initialize store.

        Args:
            max_records: Maximum number of records to keep
        """
        self.max_records = max_records
        self._executions: OrderedDict[str, ExecutionDetail] = OrderedDict()
        self._logs: dict[str, list[dict[str, Any]]] = {}

    async def save(self, execution: ExecutionDetail) -> None:
        """Save an execution record."""
        # Move to end if exists (LRU)
        if execution.execution_id in self._executions:
            self._executions.move_to_end(execution.execution_id)
        self._executions[execution.execution_id] = execution

        # Evict oldest if over limit
        while len(self._executions) > self.max_records:
            oldest_id, _ = self._executions.popitem(last=False)
            self._logs.pop(oldest_id, None)

    async def get(self, execution_id: str) -> ExecutionDetail | None:
        """Get an execution by ID."""
        return self._executions.get(execution_id)

    async def list(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExecutionDetail], int]:
        """List executions with filtering."""
        executions = list(self._executions.values())

        # Apply filters
        if workflow_id:
            executions = [e for e in executions if e.workflow_id == workflow_id]
        if status:
            executions = [e for e in executions if e.status == status]
        if since:
            executions = [e for e in executions if e.started_at >= since]
        if until:
            executions = [e for e in executions if e.started_at <= until]

        # Sort by started_at descending
        executions.sort(key=lambda e: e.started_at, reverse=True)

        # Paginate
        total = len(executions)
        start = (page - 1) * page_size
        end = start + page_size

        return executions[start:end], total

    async def get_logs(
        self,
        execution_id: str,
        level: str | None = None,
        step_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get logs for an execution."""
        logs = self._logs.get(execution_id, [])

        if level:
            logs = [log for log in logs if log.get("level") == level]
        if step_id:
            logs = [log for log in logs if log.get("step_id") == step_id]

        return logs

    async def add_log(self, execution_id: str, log: dict[str, Any]) -> None:
        """Add a log entry for an execution."""
        if execution_id not in self._logs:
            self._logs[execution_id] = []
        self._logs[execution_id].append(log)


class SQLiteExecutionStore(ExecutionStore):
    """SQLite-backed execution store for persistence."""

    def __init__(self, db_path: str):
        """Initialize store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms INTEGER,
                    inputs TEXT,
                    outputs TEXT,
                    error TEXT,
                    steps TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    component TEXT,
                    step_id TEXT,
                    tool_name TEXT,
                    message TEXT,
                    FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_workflow ON executions(workflow_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_status ON executions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_exec ON execution_logs(execution_id)")

    async def save(self, execution: ExecutionDetail) -> None:
        """Save an execution record."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO executions
                (execution_id, workflow_id, status, started_at, completed_at,
                 duration_ms, inputs, outputs, error, steps)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.workflow_id,
                    execution.status.value,
                    execution.started_at.isoformat(),
                    execution.completed_at.isoformat() if execution.completed_at else None,
                    execution.duration_ms,
                    json.dumps(execution.inputs),
                    json.dumps(execution.outputs),
                    json.dumps(execution.error.model_dump()) if execution.error else None,
                    json.dumps([s.model_dump() for s in execution.steps]),
                ),
            )

    async def get(self, execution_id: str) -> ExecutionDetail | None:
        """Get an execution by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_execution(row)

    def _row_to_execution(self, row: sqlite3.Row) -> ExecutionDetail:
        """Convert database row to ExecutionDetail."""
        from ploston_core.api.models import ErrorDetail, StepSummary

        error = None
        if row["error"]:
            error_data = json.loads(row["error"])
            error = ErrorDetail(**error_data)

        steps = []
        if row["steps"]:
            steps_data = json.loads(row["steps"])
            steps = [StepSummary(**s) for s in steps_data]

        return ExecutionDetail(
            execution_id=row["execution_id"],
            workflow_id=row["workflow_id"],
            status=ExecutionStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
            duration_ms=row["duration_ms"],
            inputs=json.loads(row["inputs"]) if row["inputs"] else {},
            outputs=json.loads(row["outputs"]) if row["outputs"] else {},
            error=error,
            steps=steps,
        )

    async def list(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExecutionDetail], int]:
        """List executions with filtering."""
        conditions = []
        params: list[Any] = []

        if workflow_id:
            conditions.append("workflow_id = ?")
            params.append(workflow_id)
        if status:
            conditions.append("status = ?")
            params.append(status.value)
        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("started_at <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            total = conn.execute(
                f"SELECT COUNT(*) FROM executions WHERE {where_clause}", params
            ).fetchone()[0]

            # Get page
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT * FROM executions
                WHERE {where_clause}
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()

            executions = [self._row_to_execution(row) for row in rows]
            return executions, total

    async def get_logs(
        self,
        execution_id: str,
        level: str | None = None,
        step_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get logs for an execution."""
        conditions = ["execution_id = ?"]
        params: list[Any] = [execution_id]

        if level:
            conditions.append("level = ?")
            params.append(level)
        if step_id:
            conditions.append("step_id = ?")
            params.append(step_id)

        where_clause = " AND ".join(conditions)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM execution_logs WHERE {where_clause} ORDER BY timestamp",
                params,
            ).fetchall()

            return [dict(row) for row in rows]
