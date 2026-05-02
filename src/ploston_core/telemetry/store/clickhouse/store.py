"""ClickHouse-backed telemetry store (S-296 / T-942, T-943).

This store implements ``TelemetryStore`` against a ClickHouse cluster managed
by the bootstrap stack. Persistence semantics:

- **save_execution**: delete-then-insert by ``execution_id``. ClickHouse does
  not support multi-table transactions; if a later insert fails after the
  deletes succeed the previous data is gone. Matches the SQLite store's
  best-effort semantics.
- **delete_*** : implemented via lightweight DELETE (ClickHouse 24.x).
  Eventually consistent — rows are tombstoned and physically removed during
  background merges. Acceptable for retention enforcement.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..base import TelemetryStore
from ..config import RedactionConfig
from ..redactor import Redactor
from ..types import ExecutionRecord, ExecutionStatus, ExecutionType
from .serialization import (
    EXECUTIONS_COLS,
    STEPS_COLS,
    TOOL_CALLS_COLS,
    assemble_execution,
    serialize_execution,
    serialize_step,
    serialize_tool_call,
)

if TYPE_CHECKING:  # pragma: no cover
    from clickhouse_connect.driver.asyncclient import AsyncClient


class ClickHouseTelemetryStore(TelemetryStore):
    """ClickHouse-backed telemetry store (drop-in for SQLite)."""

    def __init__(
        self,
        host: str,
        port: int = 8123,
        database: str = "ploston",
        username: str = "default",
        password: str = "",
        secure: bool = False,
        redaction: RedactionConfig | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._secure = secure
        self._redaction = redaction
        self._client: AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _ensure_client(self) -> AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    import clickhouse_connect

                    self._client = await clickhouse_connect.get_async_client(
                        host=self._host,
                        port=self._port,
                        database=self._database,
                        username=self._username,
                        password=self._password,
                        secure=self._secure,
                    )
        return self._client  # type: ignore[return-value]

    # ─────────────────────────────────────────────────────────────────
    # save_execution
    # ─────────────────────────────────────────────────────────────────

    async def save_execution(self, record: ExecutionRecord) -> None:
        client = await self._ensure_client()
        redactor = Redactor(self._redaction) if self._redaction else None

        exec_row = serialize_execution(record, redactor)
        step_rows = [serialize_step(record.execution_id, s, redactor) for s in record.steps]
        call_rows = [serialize_tool_call(tc, redactor) for s in record.steps for tc in s.tool_calls]

        # Delete order: children → parent. If a later insert fails the previous
        # row set is gone — matches SQLite's INSERT OR REPLACE / DELETE pattern.
        await client.command(
            "DELETE FROM ploston.tool_calls WHERE execution_id = %(id)s",
            parameters={"id": record.execution_id},
        )
        await client.command(
            "DELETE FROM ploston.steps WHERE execution_id = %(id)s",
            parameters={"id": record.execution_id},
        )
        await client.command(
            "DELETE FROM ploston.executions WHERE execution_id = %(id)s",
            parameters={"id": record.execution_id},
        )

        await client.insert("executions", [exec_row], column_names=EXECUTIONS_COLS)
        if step_rows:
            await client.insert("steps", step_rows, column_names=STEPS_COLS)
        if call_rows:
            await client.insert("tool_calls", call_rows, column_names=TOOL_CALLS_COLS)

    # ─────────────────────────────────────────────────────────────────
    # get_execution
    # ─────────────────────────────────────────────────────────────────

    async def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        client = await self._ensure_client()

        exec_result = await client.query(
            "SELECT * FROM ploston.executions WHERE execution_id = %(id)s LIMIT 1",
            parameters={"id": execution_id},
        )
        exec_rows = list(exec_result.named_results())
        if not exec_rows:
            return None

        step_result = await client.query(
            "SELECT * FROM ploston.steps WHERE execution_id = %(id)s ORDER BY started_at, attempt",
            parameters={"id": execution_id},
        )
        call_result = await client.query(
            "SELECT * FROM ploston.tool_calls WHERE execution_id = %(id)s "
            "ORDER BY step_id, sequence",
            parameters={"id": execution_id},
        )

        return assemble_execution(
            exec_rows[0],
            list(step_result.named_results()),
            list(call_result.named_results()),
        )

    # ─────────────────────────────────────────────────────────────────
    # list_executions
    # ─────────────────────────────────────────────────────────────────

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
        client = await self._ensure_client()

        clauses: list[str] = []
        params: dict[str, Any] = {}

        def _add(condition: str, key: str, value: Any) -> None:
            clauses.append(condition)
            params[key] = value

        if execution_type is not None:
            _add("execution_type = %(execution_type)s", "execution_type", execution_type.value)
        if workflow_id is not None:
            _add("workflow_id = %(workflow_id)s", "workflow_id", workflow_id)
        if tool_name is not None:
            _add("tool_name = %(tool_name)s", "tool_name", tool_name)
        if status is not None:
            _add("status = %(status)s", "status", status.value)
        if since is not None:
            _add("started_at >= %(since)s", "since", since)
        if until is not None:
            _add("started_at <= %(until)s", "until", until)
        if caller_id is not None:
            _add("caller_id = %(caller_id)s", "caller_id", caller_id)
        if session_id is not None:
            _add("session_id = %(session_id)s", "session_id", session_id)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        count_result = await client.query(
            f"SELECT count() AS c FROM ploston.executions {where}",
            parameters=params,
        )
        total = list(count_result.named_results())[0]["c"]
        if not total:
            return ([], 0)

        offset = max(0, (page - 1) * page_size)
        list_params = {**params, "limit": page_size, "offset": offset}
        rows_result = await client.query(
            f"SELECT * FROM ploston.executions {where} "
            "ORDER BY started_at DESC LIMIT %(limit)s OFFSET %(offset)s",
            parameters=list_params,
        )
        exec_rows = list(rows_result.named_results())
        if not exec_rows:
            return ([], int(total))

        ids = [r["execution_id"] for r in exec_rows]
        step_result = await client.query(
            "SELECT * FROM ploston.steps WHERE execution_id IN %(ids)s "
            "ORDER BY execution_id, started_at, attempt",
            parameters={"ids": ids},
        )
        call_result = await client.query(
            "SELECT * FROM ploston.tool_calls WHERE execution_id IN %(ids)s "
            "ORDER BY execution_id, step_id, sequence",
            parameters={"ids": ids},
        )
        steps_by_exec: dict[str, list[dict[str, Any]]] = {}
        for sr in step_result.named_results():
            steps_by_exec.setdefault(sr["execution_id"], []).append(sr)
        calls_by_exec: dict[str, list[dict[str, Any]]] = {}
        for cr in call_result.named_results():
            calls_by_exec.setdefault(cr["execution_id"], []).append(cr)

        records = [
            assemble_execution(
                er,
                steps_by_exec.get(er["execution_id"], []),
                calls_by_exec.get(er["execution_id"], []),
            )
            for er in exec_rows
        ]
        return (records, int(total))

    # ─────────────────────────────────────────────────────────────────
    # delete_execution / delete_before
    # ─────────────────────────────────────────────────────────────────

    async def delete_execution(self, execution_id: str) -> bool:
        client = await self._ensure_client()
        check = await client.query(
            "SELECT count() AS c FROM ploston.executions WHERE execution_id = %(id)s",
            parameters={"id": execution_id},
        )
        if list(check.named_results())[0]["c"] == 0:
            return False
        for table in ("tool_calls", "steps", "executions"):
            await client.command(
                f"DELETE FROM ploston.{table} WHERE execution_id = %(id)s",
                parameters={"id": execution_id},
            )
        return True

    async def delete_before(self, cutoff: datetime) -> int:
        client = await self._ensure_client()
        ids_result = await client.query(
            "SELECT execution_id FROM ploston.executions WHERE started_at < %(cutoff)s",
            parameters={"cutoff": cutoff},
        )
        ids = [r["execution_id"] for r in ids_result.named_results()]
        if not ids:
            return 0
        await client.command(
            "DELETE FROM ploston.tool_calls WHERE execution_id IN %(ids)s",
            parameters={"ids": ids},
        )
        await client.command(
            "DELETE FROM ploston.steps WHERE execution_id IN %(ids)s",
            parameters={"ids": ids},
        )
        await client.command(
            "DELETE FROM ploston.executions WHERE started_at < %(cutoff)s",
            parameters={"cutoff": cutoff},
        )
        return len(ids)

    # ─────────────────────────────────────────────────────────────────
    # get_tool_call_stats
    # ─────────────────────────────────────────────────────────────────

    async def get_tool_call_stats(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        client = await self._ensure_client()

        clauses: list[str] = []
        params: dict[str, Any] = {}
        if since is not None:
            clauses.append("started_at >= %(since)s")
            params["since"] = since
        if until is not None:
            clauses.append("started_at <= %(until)s")
            params["until"] = until
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        result = await client.query(
            f"""
            SELECT tool_name,
                   count() AS total,
                   countIf(error_code IS NULL OR error_code = '') AS success,
                   countIf(error_code IS NOT NULL AND error_code != '') AS error
            FROM ploston.tool_calls
            {where}
            GROUP BY tool_name
            """,
            parameters=params,
        )
        return {
            row["tool_name"]: {
                "total": int(row["total"]),
                "success": int(row["success"]),
                "error": int(row["error"]),
            }
            for row in result.named_results()
        }

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
