"""ExecutionRecord ↔ ClickHouse row mapping for ploston.{executions,steps,tool_calls}.

S-296 / T-943. Helpers are pure functions so the store class stays focused on
I/O and the assembly logic can be unit-tested without a live ClickHouse.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..redactor import Redactor
from ..types import (
    ErrorRecord,
    ExecutionRecord,
    ExecutionStatus,
    ExecutionType,
    StepRecord,
    StepStatus,
    StepType,
    ToolCallRecord,
    ToolCallSource,
)

# Used as a synthetic value for executions that are still pending; ClickHouse
# DDL declares ``started_at`` as a non-nullable DateTime64 so we must supply *something*.
_EPOCH = datetime.fromtimestamp(0, tz=UTC)

# Column orderings that match the DDL exactly. Used for client.insert(...)
# so the driver can ship rows as positional tuples without a name lookup.
EXECUTIONS_COLS = [
    "execution_id",
    "execution_type",
    "workflow_id",
    "workflow_version",
    "tool_name",
    "status",
    "started_at",
    "completed_at",
    "duration_ms",
    "inputs",
    "outputs",
    "inputs_bytes",
    "outputs_bytes",
    "error_code",
    "error_category",
    "error_message",
    "source",
    "caller_id",
    "tenant_id",
    "session_id",
    "runner_id",
    "bridge_session_id",
    "step_count",
    "tool_call_count",
    "total_response_bytes",
]

STEPS_COLS = [
    "execution_id",
    "step_id",
    "attempt",
    "step_type",
    "status",
    "skip_reason",
    "started_at",
    "completed_at",
    "duration_ms",
    "tool_name",
    "tool_params",
    "tool_result",
    "code_hash",
    "error_code",
    "error_message",
    "max_attempts",
]

TOOL_CALLS_COLS = [
    "execution_id",
    "step_id",
    "call_id",
    "tool_name",
    "started_at",
    "completed_at",
    "duration_ms",
    "params",
    "params_bytes",
    "result",
    "response_bytes",
    "error_code",
    "error_category",
    "error_message",
    "source",
    "runner_id",
    "bridge_id",
    "session_id",
    "sequence",
]


def _redact(value: Any, redactor: Redactor | None) -> Any:
    return redactor.redact(value) if redactor else value


def _json(value: Any) -> str:
    """Serialize value to a JSON string, with stable handling of non-JSON types."""
    if value is None:
        return ""
    return json.dumps(value, default=str)


def serialize_execution(record: ExecutionRecord, redactor: Redactor | None) -> list[Any]:
    err = record.error
    return [
        record.execution_id,
        record.execution_type.value,
        record.workflow_id,
        record.workflow_version,
        record.tool_name,
        record.status.value,
        record.started_at or _EPOCH,
        record.completed_at,
        record.duration_ms,
        _json(_redact(record.inputs, redactor)),
        _json(_redact(record.outputs, redactor)),
        record.inputs_bytes,
        record.outputs_bytes,
        err.code if err else None,
        err.category if err else None,
        err.message if err else None,
        record.source,
        record.caller_id,
        record.tenant_id,
        record.session_id,
        record.runner_id,
        record.bridge_session_id,
        record.step_count,
        record.tool_call_count,
        record.total_response_bytes,
    ]


def serialize_step(execution_id: str, step: StepRecord, redactor: Redactor | None) -> list[Any]:
    err = step.error
    return [
        execution_id,
        step.step_id,
        step.attempt,
        step.step_type.value,
        step.status.value,
        step.skip_reason,
        step.started_at,
        step.completed_at,
        step.duration_ms,
        step.tool_name,
        _json(_redact(step.tool_params, redactor)),
        _json(_redact(step.tool_result, redactor)),
        step.code_hash,
        err.code if err else None,
        err.message if err else None,
        step.max_attempts,
    ]


def _parse_json(value: str | None) -> Any:
    if not value:
        return {} if value == "" else None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _err(code: Any, category: Any, message: Any) -> ErrorRecord | None:
    if not code and not message:
        return None
    return ErrorRecord(
        code=str(code or ""),
        category=str(category or ""),
        message=str(message or ""),
    )


def deserialize_execution(row: dict[str, Any]) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=row["execution_id"],
        execution_type=ExecutionType(row["execution_type"]),
        workflow_id=row.get("workflow_id"),
        workflow_version=row.get("workflow_version"),
        tool_name=row.get("tool_name"),
        status=ExecutionStatus(row["status"]),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        duration_ms=row.get("duration_ms"),
        inputs=_parse_json(row.get("inputs")) or {},
        outputs=_parse_json(row.get("outputs")) or {},
        error=_err(row.get("error_code"), row.get("error_category"), row.get("error_message")),
        source=row.get("source") or "mcp",
        caller_id=row.get("caller_id"),
        tenant_id=row.get("tenant_id"),
        session_id=row.get("session_id"),
        runner_id=row.get("runner_id"),
        bridge_session_id=row.get("bridge_session_id"),
        inputs_bytes=row.get("inputs_bytes") or 0,
        outputs_bytes=row.get("outputs_bytes") or 0,
        step_count=row.get("step_count") or 0,
        tool_call_count=row.get("tool_call_count") or 0,
        total_response_bytes=row.get("total_response_bytes") or 0,
    )


def deserialize_step(row: dict[str, Any]) -> StepRecord:
    return StepRecord(
        step_id=row["step_id"],
        step_type=StepType(row["step_type"]),
        status=StepStatus(row["status"]),
        skip_reason=row.get("skip_reason"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        duration_ms=row.get("duration_ms"),
        tool_name=row.get("tool_name"),
        tool_params=_parse_json(row.get("tool_params")),
        tool_result=_parse_json(row.get("tool_result")),
        code_hash=row.get("code_hash"),
        error=_err(row.get("error_code"), None, row.get("error_message")),
        attempt=row.get("attempt") or 1,
        max_attempts=row.get("max_attempts") or 1,
    )


def deserialize_tool_call(row: dict[str, Any]) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=row["call_id"],
        tool_name=row["tool_name"],
        started_at=row["started_at"],
        completed_at=row.get("completed_at"),
        duration_ms=row.get("duration_ms"),
        params=_parse_json(row.get("params")),
        result=_parse_json(row.get("result")),
        error=_err(row.get("error_code"), row.get("error_category"), row.get("error_message")),
        execution_id=row["execution_id"],
        step_id=row["step_id"],
        source=ToolCallSource(row.get("source") or "tool_step"),
        sequence=row.get("sequence") or 0,
        params_bytes=row.get("params_bytes") or 0,
        response_bytes=row.get("response_bytes") or 0,
        runner_id=row.get("runner_id"),
        bridge_id=row.get("bridge_id"),
        session_id=row.get("session_id"),
    )


def assemble_execution(
    exec_row: dict[str, Any],
    step_rows: list[dict[str, Any]],
    call_rows: list[dict[str, Any]],
) -> ExecutionRecord:
    record = deserialize_execution(exec_row)
    steps_by_id: dict[tuple[str, int], StepRecord] = {}
    for sr in step_rows:
        step = deserialize_step(sr)
        steps_by_id[(step.step_id, step.attempt)] = step
        record.steps.append(step)
    for cr in call_rows:
        call = deserialize_tool_call(cr)
        # match call to its step (latest attempt seen wins, matches SQLite behavior)
        for (sid, _att), step in steps_by_id.items():
            if sid == call.step_id:
                step.tool_calls.append(call)
                break
    return record


def serialize_tool_call(call: ToolCallRecord, redactor: Redactor | None) -> list[Any]:
    err = call.error
    return [
        call.execution_id,
        call.step_id,
        call.call_id,
        call.tool_name,
        call.started_at,
        call.completed_at,
        call.duration_ms,
        _json(_redact(call.params, redactor)),
        call.params_bytes,
        _json(_redact(call.result, redactor)),
        call.response_bytes,
        err.code if err else None,
        err.category if err else None,
        err.message if err else None,
        call.source.value,
        call.runner_id,
        call.bridge_id,
        call.session_id,
        call.sequence,
    ]
