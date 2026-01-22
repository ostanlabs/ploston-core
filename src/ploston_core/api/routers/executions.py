"""Execution router."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, Query, Request

from ploston_core.api.models import (
    ExecutionDetail,
    ExecutionListResponse,
    ExecutionLogsResponse,
    ExecutionStatus,
    ExecutionSummary,
    LogEntry,
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
    store = request.app.state.execution_store

    if not store:
        return ExecutionListResponse(
            executions=[],
            total=0,
            page=page,
            page_size=page_size,
            has_next=False,
            has_prev=False,
        )

    executions, total = await store.list(
        workflow_id=workflow_id,
        status=status,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )

    summaries = [
        ExecutionSummary(
            execution_id=e.execution_id,
            workflow_id=e.workflow_id,
            status=e.status,
            started_at=e.started_at,
            completed_at=e.completed_at,
            duration_ms=e.duration_ms,
        )
        for e in executions
    ]

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
    store = request.app.state.execution_store

    if not store:
        raise HTTPException(status_code=404, detail="Execution store not configured")

    execution = await store.get(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")

    return execution


@execution_router.get("/{execution_id}/logs", response_model=ExecutionLogsResponse)
async def get_execution_logs(
    request: Request,
    execution_id: str = Path(...),
    level: str | None = Query(default="INFO"),
    step_id: str | None = None,
) -> ExecutionLogsResponse:
    """Get execution logs."""
    store = request.app.state.execution_store

    if not store:
        raise HTTPException(status_code=404, detail="Execution store not configured")

    execution = await store.get(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")

    # Get logs from store
    logs = await store.get_logs(execution_id, level=level, step_id=step_id)

    return ExecutionLogsResponse(
        execution_id=execution_id,
        logs=[
            LogEntry(
                timestamp=log.get("timestamp", datetime.now()),
                level=log.get("level", "INFO"),
                component=log.get("component", "unknown"),
                step_id=log.get("step_id"),
                tool_name=log.get("tool_name"),
                message=log.get("message", ""),
            )
            for log in logs
        ],
    )

