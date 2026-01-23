"""Workflow router."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request, Response

from ploston_core.api.models import (
    ExecuteRequest,
    ExecutionDetail,
    ExecutionStatus,
    StepSummary,
    ValidationError,
    ValidationResult,
    WorkflowCreateResponse,
    WorkflowDetail,
    WorkflowListResponse,
    WorkflowStatus,
    WorkflowSummary,
)
from ploston_core.errors import AELError
from ploston_core.workflow import WorkflowDefinition, parse_workflow_yaml

workflow_router = APIRouter(prefix="/workflows", tags=["Workflows"])


def _workflow_to_summary(workflow: WorkflowDefinition) -> WorkflowSummary:
    """Convert WorkflowDefinition to WorkflowSummary."""
    now = datetime.now(UTC)
    return WorkflowSummary(
        id=workflow.name,
        name=workflow.name,
        version=workflow.version,
        description=workflow.description,
        status=WorkflowStatus.ACTIVE,
        tags=workflow.tags or [],
        inputs=list(workflow.inputs.keys()) if workflow.inputs else [],
        created_at=now,
        updated_at=now,
    )


def _workflow_to_detail(workflow: WorkflowDefinition, yaml_content: str) -> WorkflowDetail:
    """Convert WorkflowDefinition to WorkflowDetail."""
    now = datetime.now(UTC)
    return WorkflowDetail(
        id=workflow.name,
        name=workflow.name,
        version=workflow.version,
        description=workflow.description,
        status=WorkflowStatus.ACTIVE,
        tags=workflow.tags or [],
        definition={
            "inputs": workflow.inputs,
            "steps": [s.__dict__ for s in workflow.steps],
            "outputs": workflow.outputs,
        },
        yaml=yaml_content,
        created_at=now,
        updated_at=now,
    )


@workflow_router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    request: Request,
    status: WorkflowStatus | None = None,
    tag: str | None = None,
    search: str | None = None,
    sort: str = "-updated_at",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> WorkflowListResponse:
    """List all workflows with optional filtering."""
    registry = request.app.state.workflow_registry
    workflows = registry.list_workflows()

    # Filter by tag
    if tag:
        workflows = [w for w in workflows if tag in (w.tags or [])]

    # Filter by search (name or description)
    if search:
        search_lower = search.lower()
        workflows = [
            w
            for w in workflows
            if search_lower in w.name.lower()
            or (w.description and search_lower in w.description.lower())
        ]

    # Convert to summaries
    summaries = [_workflow_to_summary(w) for w in workflows]

    # Pagination
    total = len(summaries)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = summaries[start:end]

    return WorkflowListResponse(
        workflows=page_items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=end < total,
        has_prev=page > 1,
    )


@workflow_router.post("", response_model=WorkflowCreateResponse, status_code=201)
async def create_workflow(
    request: Request,
    yaml_content: Annotated[str, Body(media_type="application/x-yaml")],
) -> WorkflowCreateResponse:
    """Register a new workflow from YAML."""
    registry = request.app.state.workflow_registry

    try:
        workflow = parse_workflow_yaml(yaml_content)
        registry.register(workflow, validate=True)

        return WorkflowCreateResponse(
            id=workflow.name,
            name=workflow.name,
            version=workflow.version,
            status=WorkflowStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    except AELError as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_dict())


@workflow_router.post("/validate", response_model=ValidationResult)
async def validate_workflow(
    request: Request,
    yaml_content: Annotated[str, Body(media_type="application/x-yaml")],
) -> ValidationResult:
    """Validate workflow YAML without registering."""
    registry = request.app.state.workflow_registry

    try:
        workflow = parse_workflow_yaml(yaml_content)
        result = registry._validator.validate(workflow)

        return ValidationResult(
            valid=result.valid,
            errors=[
                ValidationError(path=e.path, message=e.message, line=getattr(e, "line", None))
                for e in result.errors
            ],
            warnings=[
                ValidationError(path=w.path, message=w.message, line=getattr(w, "line", None))
                for w in result.warnings
            ],
        )
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[ValidationError(path="yaml", message=str(e))],
        )


@workflow_router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    request: Request,
    workflow_id: str = Path(..., description="Workflow ID"),
) -> WorkflowDetail:
    """Get workflow details."""
    registry = request.app.state.workflow_registry
    workflow = registry.get(workflow_id)

    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    # We don't store original YAML, so reconstruct a basic version
    yaml_repr = f"name: {workflow.name}\nversion: {workflow.version}"
    return _workflow_to_detail(workflow, yaml_repr)


@workflow_router.put("/{workflow_id}", response_model=WorkflowCreateResponse)
async def update_workflow(
    request: Request,
    workflow_id: str = Path(...),
    yaml_content: Annotated[str, Body(media_type="application/x-yaml")] = "",
) -> WorkflowCreateResponse:
    """Update an existing workflow."""
    registry = request.app.state.workflow_registry

    # Check if workflow exists
    existing = registry.get(workflow_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    try:
        workflow = parse_workflow_yaml(yaml_content)

        # Ensure the name matches
        if workflow.name != workflow_id:
            raise HTTPException(
                status_code=400,
                detail=f"Workflow name '{workflow.name}' does not match ID '{workflow_id}'",
            )

        # Unregister old and register new
        registry.unregister(workflow_id)
        registry.register(workflow, validate=True)

        return WorkflowCreateResponse(
            id=workflow.name,
            name=workflow.name,
            version=workflow.version,
            status=WorkflowStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    except AELError as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_dict())


@workflow_router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    request: Request,
    workflow_id: str = Path(...),
) -> Response:
    """Delete a workflow."""
    registry = request.app.state.workflow_registry

    if not registry.unregister(workflow_id):
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    return Response(status_code=204)


@workflow_router.post("/{workflow_id}/execute", response_model=ExecutionDetail)
async def execute_workflow(
    request: Request,
    workflow_id: str = Path(...),
    execute_request: ExecuteRequest = Body(...),
) -> ExecutionDetail:
    """Execute a workflow synchronously."""
    engine = request.app.state.workflow_engine
    store = request.app.state.execution_store

    try:
        result = await engine.execute(workflow_id, execute_request.inputs)

        # Convert to ExecutionDetail
        execution = ExecutionDetail(
            execution_id=result.execution_id,
            workflow_id=workflow_id,
            status=ExecutionStatus(result.status.value),
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            inputs=execute_request.inputs,
            outputs=result.outputs or {},
            error=None,
            steps=[
                StepSummary(
                    id=s.step_id,
                    tool=s.tool_name,
                    type="tool" if s.tool_name else "code",
                    status=ExecutionStatus(s.status.value),
                    started_at=s.started_at,
                    completed_at=s.completed_at,
                    duration_ms=s.duration_ms,
                    error=str(s.error) if s.error else None,
                )
                for s in result.steps
            ],
        )

        # Store execution
        if store:
            await store.save(execution)

        return execution

    except AELError as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_dict())
