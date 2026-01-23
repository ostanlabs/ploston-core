"""SQLite-based telemetry store."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import TelemetryStore
from .config import RedactionConfig
from .types import (
    ErrorRecord,
    ExecutionMetrics,
    ExecutionRecord,
    ExecutionStatus,
    ExecutionType,
    StepRecord,
    StepStatus,
    StepType,
    ToolCallRecord,
    ToolCallSource,
)


class SQLiteTelemetryStore(TelemetryStore):
    """SQLite-based telemetry store.

    Persists data to disk. Suitable for OSS production use.
    """

    def __init__(
        self,
        db_path: str,
        redaction: RedactionConfig | None = None,
    ) -> None:
        """Initialize SQLite store.

        Args:
            db_path: Path to SQLite database file
            redaction: Optional redaction configuration
        """
        self._db_path = db_path
        self._redaction = redaction
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._conn: sqlite3.Connection | None = None

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                execution_type TEXT NOT NULL,
                workflow_id TEXT,
                workflow_version TEXT,
                tool_name TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms INTEGER,
                inputs TEXT,
                outputs TEXT,
                error TEXT,
                metrics TEXT,
                source TEXT,
                caller_id TEXT,
                tenant_id TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT REFERENCES executions(execution_id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                step_type TEXT NOT NULL,
                status TEXT NOT NULL,
                skip_reason TEXT,
                started_at TEXT,
                completed_at TEXT,
                duration_ms INTEGER,
                tool_name TEXT,
                tool_params TEXT,
                tool_result TEXT,
                code_hash TEXT,
                error TEXT,
                attempt INTEGER DEFAULT 1,
                max_attempts INTEGER DEFAULT 1,
                UNIQUE(execution_id, step_id, attempt)
            );

            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT REFERENCES executions(execution_id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms INTEGER,
                params TEXT,
                result TEXT,
                error TEXT,
                source TEXT NOT NULL,
                sequence INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_executions_type ON executions(execution_type);
            CREATE INDEX IF NOT EXISTS idx_executions_workflow ON executions(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
            CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at);
            CREATE INDEX IF NOT EXISTS idx_executions_session ON executions(session_id);
            CREATE INDEX IF NOT EXISTS idx_steps_execution ON steps(execution_id);
            CREATE INDEX IF NOT EXISTS idx_tool_calls_execution ON tool_calls(execution_id);
            CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_name);
        """
        )
        self._conn.commit()

    async def save_execution(self, record: ExecutionRecord) -> None:
        """Save execution record with steps and tool calls."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._save_sync, record)

    def _save_sync(self, record: ExecutionRecord) -> None:
        """Synchronous save (runs in thread pool)."""
        if not self._conn:
            return
        cursor = self._conn.cursor()

        # Upsert execution
        cursor.execute(
            """
            INSERT OR REPLACE INTO executions (
                execution_id, execution_type, workflow_id, workflow_version,
                tool_name, status, started_at, completed_at, duration_ms,
                inputs, outputs, error, metrics, source, caller_id,
                tenant_id, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                record.execution_id,
                record.execution_type.value,
                record.workflow_id,
                record.workflow_version,
                record.tool_name,
                record.status.value,
                record.started_at.isoformat() if record.started_at else None,
                record.completed_at.isoformat() if record.completed_at else None,
                record.duration_ms,
                json.dumps(record.inputs),
                json.dumps(record.outputs),
                json.dumps(self._error_to_dict(record.error)) if record.error else None,
                json.dumps(self._metrics_to_dict(record.metrics)),
                record.source,
                record.caller_id,
                record.tenant_id,
                record.session_id,
            ),
        )

        # Delete existing steps and tool calls (for updates)
        cursor.execute("DELETE FROM steps WHERE execution_id = ?", (record.execution_id,))
        cursor.execute("DELETE FROM tool_calls WHERE execution_id = ?", (record.execution_id,))

        # Insert steps
        for step in record.steps:
            cursor.execute(
                """
                INSERT INTO steps (
                    execution_id, step_id, step_type, status, skip_reason,
                    started_at, completed_at, duration_ms, tool_name,
                    tool_params, tool_result, code_hash, error, attempt, max_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    record.execution_id,
                    step.step_id,
                    step.step_type.value,
                    step.status.value,
                    step.skip_reason,
                    step.started_at.isoformat() if step.started_at else None,
                    step.completed_at.isoformat() if step.completed_at else None,
                    step.duration_ms,
                    step.tool_name,
                    json.dumps(step.tool_params) if step.tool_params else None,
                    json.dumps(step.tool_result) if step.tool_result else None,
                    step.code_hash,
                    json.dumps(self._error_to_dict(step.error)) if step.error else None,
                    step.attempt,
                    step.max_attempts,
                ),
            )

            # Insert tool calls
            for call in step.tool_calls:
                cursor.execute(
                    """
                    INSERT INTO tool_calls (
                        execution_id, step_id, call_id, tool_name,
                        started_at, completed_at, duration_ms,
                        params, result, error, source, sequence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.execution_id,
                        step.step_id,
                        call.call_id,
                        call.tool_name,
                        call.started_at.isoformat(),
                        call.completed_at.isoformat() if call.completed_at else None,
                        call.duration_ms,
                        json.dumps(call.params) if call.params else None,
                        json.dumps(call.result) if call.result else None,
                        (json.dumps(self._error_to_dict(call.error)) if call.error else None),
                        call.source.value,
                        call.sequence,
                    ),
                )

        self._conn.commit()

    async def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        """Get execution by ID."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._get_sync, execution_id)

    def _get_sync(self, execution_id: str) -> ExecutionRecord | None:
        """Synchronous get."""
        if not self._conn:
            return None
        cursor = self._conn.cursor()

        # Get execution
        row = cursor.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()

        if not row:
            return None

        record = self._row_to_execution(row)

        # Get steps
        step_rows = cursor.execute(
            "SELECT * FROM steps WHERE execution_id = ? ORDER BY id", (execution_id,)
        ).fetchall()

        for step_row in step_rows:
            step = self._row_to_step(step_row)

            # Get tool calls for step
            call_rows = cursor.execute(
                "SELECT * FROM tool_calls WHERE execution_id = ? AND step_id = ? ORDER BY sequence",
                (execution_id, step.step_id),
            ).fetchall()

            step.tool_calls = [self._row_to_tool_call(r) for r in call_rows]
            record.steps.append(step)

        return record

    async def list_executions(
        self,
        execution_type: ExecutionType | None = None,
        workflow_id: str | None = None,
        tool_name: str | None = None,
        status: ExecutionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        caller_id: str | None = None,
        session_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExecutionRecord], int]:
        """List executions with filtering."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._list_sync,
            execution_type,
            workflow_id,
            tool_name,
            status,
            since,
            until,
            caller_id,
            session_id,
            page,
            page_size,
        )

    def _list_sync(
        self,
        execution_type: ExecutionType | None,
        workflow_id: str | None,
        tool_name: str | None,
        status: ExecutionStatus | None,
        since: datetime | None,
        until: datetime | None,
        caller_id: str | None,
        session_id: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ExecutionRecord], int]:
        """Synchronous list."""
        if not self._conn:
            return [], 0
        cursor = self._conn.cursor()

        # Build query
        conditions = []
        params: list[Any] = []

        if execution_type:
            conditions.append("execution_type = ?")
            params.append(execution_type.value)
        if workflow_id:
            conditions.append("workflow_id = ?")
            params.append(workflow_id)
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if status:
            conditions.append("status = ?")
            params.append(status.value)
        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("started_at <= ?")
            params.append(until.isoformat())
        if caller_id:
            conditions.append("caller_id = ?")
            params.append(caller_id)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        where = " AND ".join(conditions) if conditions else "1=1"

        # Count total
        count_row = cursor.execute(
            f"SELECT COUNT(*) FROM executions WHERE {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        # Get page
        offset = (page - 1) * page_size
        rows = cursor.execute(
            f"SELECT * FROM executions WHERE {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

        records = [self._row_to_execution(row) for row in rows]
        return records, total

    async def delete_execution(self, execution_id: str) -> bool:
        """Delete an execution."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._delete_sync, execution_id)

    def _delete_sync(self, execution_id: str) -> bool:
        """Synchronous delete."""
        if not self._conn:
            return False
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM executions WHERE execution_id = ?", (execution_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    async def delete_before(self, cutoff: datetime) -> int:
        """Delete executions before cutoff."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._delete_before_sync, cutoff)

    def _delete_before_sync(self, cutoff: datetime) -> int:
        """Synchronous delete before."""
        if not self._conn:
            return 0
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM executions WHERE started_at < ?", (cutoff.isoformat(),))
        self._conn.commit()
        return cursor.rowcount

    async def get_tool_call_stats(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        """Get tool call statistics."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._stats_sync, since, until)

    def _stats_sync(
        self,
        since: datetime | None,
        until: datetime | None,
    ) -> dict[str, dict[str, int]]:
        """Synchronous stats."""
        if not self._conn:
            return {}
        cursor = self._conn.cursor()

        conditions = []
        params: list[Any] = []

        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("started_at <= ?")
            params.append(until.isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = cursor.execute(
            f"""
            SELECT tool_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) as success,
                   SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error
            FROM tool_calls
            WHERE {where}
            GROUP BY tool_name
        """,
            params,
        ).fetchall()

        return {
            row["tool_name"]: {
                "total": row["total"],
                "success": row["success"],
                "error": row["error"],
            }
            for row in rows
        }

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._executor.shutdown(wait=False)

    # ─────────────────────────────────────────────────────────────────
    # Helper methods
    # ─────────────────────────────────────────────────────────────────

    def _error_to_dict(self, error: ErrorRecord) -> dict[str, Any]:
        """Convert ErrorRecord to dict."""
        return {
            "code": error.code,
            "category": error.category,
            "message": error.message,
            "detail": error.detail,
            "retryable": error.retryable,
            "step_id": error.step_id,
            "tool_name": error.tool_name,
            "cause": self._error_to_dict(error.cause) if error.cause else None,
        }

    def _dict_to_error(self, data: dict[str, Any]) -> ErrorRecord:
        """Convert dict to ErrorRecord."""
        return ErrorRecord(
            code=data["code"],
            category=data["category"],
            message=data["message"],
            detail=data.get("detail"),
            retryable=data.get("retryable", False),
            step_id=data.get("step_id"),
            tool_name=data.get("tool_name"),
            cause=self._dict_to_error(data["cause"]) if data.get("cause") else None,
        )

    def _metrics_to_dict(self, metrics: ExecutionMetrics) -> dict[str, Any]:
        """Convert ExecutionMetrics to dict."""
        return {
            "total_steps": metrics.total_steps,
            "completed_steps": metrics.completed_steps,
            "failed_steps": metrics.failed_steps,
            "skipped_steps": metrics.skipped_steps,
            "total_tool_calls": metrics.total_tool_calls,
            "tool_call_breakdown": metrics.tool_call_breakdown,
            "total_duration_ms": metrics.total_duration_ms,
            "step_durations_ms": metrics.step_durations_ms,
        }

    def _dict_to_metrics(self, data: dict[str, Any]) -> ExecutionMetrics:
        """Convert dict to ExecutionMetrics."""
        return ExecutionMetrics(
            total_steps=data.get("total_steps", 0),
            completed_steps=data.get("completed_steps", 0),
            failed_steps=data.get("failed_steps", 0),
            skipped_steps=data.get("skipped_steps", 0),
            total_tool_calls=data.get("total_tool_calls", 0),
            tool_call_breakdown=data.get("tool_call_breakdown", {}),
            total_duration_ms=data.get("total_duration_ms", 0),
            step_durations_ms=data.get("step_durations_ms", {}),
        )

    def _row_to_execution(self, row: sqlite3.Row) -> ExecutionRecord:
        """Convert database row to ExecutionRecord."""
        return ExecutionRecord(
            execution_id=row["execution_id"],
            execution_type=ExecutionType(row["execution_type"]),
            workflow_id=row["workflow_id"],
            workflow_version=row["workflow_version"],
            tool_name=row["tool_name"],
            status=ExecutionStatus(row["status"]),
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            duration_ms=row["duration_ms"],
            inputs=json.loads(row["inputs"]) if row["inputs"] else {},
            outputs=json.loads(row["outputs"]) if row["outputs"] else {},
            error=(self._dict_to_error(json.loads(row["error"])) if row["error"] else None),
            metrics=(
                self._dict_to_metrics(json.loads(row["metrics"]))
                if row["metrics"]
                else ExecutionMetrics()
            ),
            source=row["source"] or "mcp",
            caller_id=row["caller_id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
        )

    def _row_to_step(self, row: sqlite3.Row) -> StepRecord:
        """Convert database row to StepRecord."""
        return StepRecord(
            step_id=row["step_id"],
            step_type=StepType(row["step_type"]),
            status=StepStatus(row["status"]),
            skip_reason=row["skip_reason"],
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            duration_ms=row["duration_ms"],
            tool_name=row["tool_name"],
            tool_params=json.loads(row["tool_params"]) if row["tool_params"] else None,
            tool_result=json.loads(row["tool_result"]) if row["tool_result"] else None,
            code_hash=row["code_hash"],
            error=(self._dict_to_error(json.loads(row["error"])) if row["error"] else None),
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
        )

    def _row_to_tool_call(self, row: sqlite3.Row) -> ToolCallRecord:
        """Convert database row to ToolCallRecord."""
        return ToolCallRecord(
            call_id=row["call_id"],
            tool_name=row["tool_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            duration_ms=row["duration_ms"],
            params=json.loads(row["params"]) if row["params"] else None,
            result=json.loads(row["result"]) if row["result"] else None,
            error=(self._dict_to_error(json.loads(row["error"])) if row["error"] else None),
            execution_id=row["execution_id"],
            step_id=row["step_id"],
            source=ToolCallSource(row["source"]),
            sequence=row["sequence"],
        )
