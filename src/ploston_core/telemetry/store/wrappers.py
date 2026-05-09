"""S-304 — Async context manager wrappers around TelemetryCollector.

Centralizes the start/end lifecycle for tool calls so call sites don't
sprinkle try/except blocks. Telemetry failures are logged at WARN and
never propagate — observability must not break user-facing tool calls.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from .types import ErrorRecord, StepStatus, StepType, ToolCallSource

if TYPE_CHECKING:
    from .collector import TelemetryCollector

_log = logging.getLogger(__name__)


class _ToolCallHandle:
    """Mutable handle yielded inside ``record_tool_call``.

    Callers populate ``result`` / ``error`` before context exit; the
    helper pushes them through ``end_tool_call`` automatically.
    """

    __slots__ = ("result", "error", "_response_bytes")

    def __init__(self) -> None:
        self.result: dict[str, Any] | Any | None = None
        self.error: ErrorRecord | None = None
        self._response_bytes: int | None = None

    def set_result(self, value: Any) -> None:
        self.result = value

    def set_error(self, err: ErrorRecord | Exception | None) -> None:
        if err is None or isinstance(err, ErrorRecord):
            self.error = err  # type: ignore[assignment]
            return
        # Coerce arbitrary exception into ErrorRecord shape.
        # For AELError (and similar structured errors) the short
        # ``message`` is often generic ("Invalid tool input") while the
        # ``detail`` carries actionable context.  Concatenate them so
        # the ClickHouse ``error_message`` column is useful in
        # dashboards without requiring a drill-down.
        base_msg = getattr(err, "message", None) or str(err)
        detail = getattr(err, "detail", None)
        suggestion = getattr(err, "suggestion", None)
        parts = [base_msg]
        if detail:
            parts.append(str(detail))
        if suggestion:
            parts.append(f"Suggestion: {suggestion}")
        full_message = " — ".join(parts)

        category_raw = getattr(err, "category", "tool")
        # ErrorCategory is a str enum; coerce to plain string for
        # ErrorRecord which expects ``str``.
        category_str = category_raw.value if hasattr(category_raw, "value") else str(category_raw)

        self.error = ErrorRecord(
            code=getattr(err, "code", type(err).__name__),
            category=category_str,
            message=full_message,
            detail=str(detail) if detail else None,
        )


@asynccontextmanager
async def record_tool_call(
    collector: TelemetryCollector | None,
    *,
    execution_id: str | None,
    step_id: str,
    tool_name: str,
    params: dict[str, Any] | None,
    source: ToolCallSource,
    runner_id: str | None = None,
    bridge_id: str | None = None,
    session_id: str | None = None,
) -> AsyncIterator[_ToolCallHandle]:
    """Wrap a tool invocation with start_tool_call / end_tool_call.

    No-op when ``collector`` or ``execution_id`` is None — keeps call
    sites unconditional. Failures are swallowed with a WARN log so
    telemetry outages never cascade into user-visible failures.
    """
    handle = _ToolCallHandle()

    call_id: str = ""
    if collector is not None and execution_id:
        try:
            call_id = await collector.start_tool_call(
                execution_id=execution_id,
                step_id=step_id,
                tool_name=tool_name,
                params=params,
                source=source,
                runner_id=runner_id,
                bridge_id=bridge_id,
                session_id=session_id,
            )
        except Exception as e:  # pragma: no cover - defensive
            _log.warning(
                "telemetry start_tool_call failed: tool=%s err=%s",
                tool_name,
                e,
            )

    try:
        yield handle
    finally:
        if collector is not None and execution_id and call_id:
            try:
                result_payload = (
                    handle.result
                    if isinstance(handle.result, dict) or handle.result is None
                    else {"value": handle.result}
                )
                await collector.end_tool_call(
                    execution_id=execution_id,
                    step_id=step_id,
                    call_id=call_id,
                    result=result_payload,
                    error=handle.error,
                )
            except Exception as e:  # pragma: no cover - defensive
                _log.warning(
                    "telemetry end_tool_call failed: tool=%s err=%s",
                    tool_name,
                    e,
                )


@asynccontextmanager
async def synthetic_direct_step(
    collector: TelemetryCollector | None,
    *,
    execution_id: str | None,
    tool_name: str,
) -> AsyncIterator[str]:
    """Open and close a synthetic ``step_id='direct'`` row (R2).

    Direct executions (MCPFrontend, REST, workflow_call_tool) don't
    have natural step rows but ``tool_calls.step_id`` is required.
    This helper writes a single synthetic step per direct execution.

    Yields the step_id (always ``"direct"``). Closes with COMPLETED on
    normal exit, FAILED on exception.
    """
    step_id = "direct"
    if collector is not None and execution_id:
        try:
            await collector.start_step(
                execution_id=execution_id,
                step_id=step_id,
                step_type=StepType.TOOL,
                tool_name=tool_name,
            )
        except Exception as e:  # pragma: no cover - defensive
            _log.warning("telemetry start_step(direct) failed: %s", e)

    failed = False
    try:
        yield step_id
    except Exception:
        failed = True
        raise
    finally:
        if collector is not None and execution_id:
            try:
                await collector.end_step(
                    execution_id=execution_id,
                    step_id=step_id,
                    status=StepStatus.FAILED if failed else StepStatus.COMPLETED,
                )
            except Exception as e:  # pragma: no cover - defensive
                _log.warning("telemetry end_step(direct) failed: %s", e)
