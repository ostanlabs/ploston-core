"""Execution router.

Routes /api/v1/executions to the canonical TelemetryStore (DEC-148).
The old ExecutionStore has been retired — all execution data now comes
from TelemetryStore which captures every execution path (MCP, REST, CLI).
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, Query, Request

from ploston_core.api.models import (
    ExecutionDetail,
    ExecutionListResponse,
    ExecutionStatus,
)
from ploston_core.api.routers.execution_adapter import (
    to_execution_detail,
    to_execution_summary,
)
from ploston_core.telemetry.store.types import (
    ExecutionStatus as TelemetryExecutionStatus,
)

execution_router = APIRouter(prefix="/executions", tags=["Executions"])


@execution_router.get("", response_model=ExecutionListResponse)
async def list_executions(
    request: Request,
    workflow_id: str | None = None,
    status: ExecutionStatus | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sort: str = "-started_at",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ExecutionListResponse:
    """List executions with optional filtering."""
    store = request.app.state.telemetry_store

    if not store:
        return ExecutionListResponse(
            executions=[],
            total=0,
            page=page,
            page_size=page_size,
            has_next=False,
            has_prev=False,
        )

    # Map API ExecutionStatus to TelemetryStore ExecutionStatus
    telemetry_status = TelemetryExecutionStatus(status.value) if status else None

    records, total = await store.list_executions(
        workflow_id=workflow_id,
        status=telemetry_status,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )

    summaries = [to_execution_summary(r) for r in records]

    return ExecutionListResponse(
        executions=summaries,
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
        has_prev=page > 1,
    )


@execution_router.get("/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    request: Request,
    execution_id: str = Path(...),
) -> ExecutionDetail:
    """Get execution details."""
    store = request.app.state.telemetry_store

    if not store:
        raise HTTPException(status_code=404, detail="Telemetry store not configured")

    record = await store.get_execution(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")

    return to_execution_detail(record)
