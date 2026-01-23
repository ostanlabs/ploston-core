"""Workflow REST API models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class WorkflowStatus(str, Enum):
    """Workflow status enum."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class WorkflowSummary(BaseModel):
    """Workflow summary for list response."""

    id: str
    name: str
    version: str
    description: str | None = None
    status: WorkflowStatus
    tags: list[str] = []
    inputs: list[str] = []
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(BaseModel):
    """Full workflow details."""

    id: str
    name: str
    version: str
    description: str | None = None
    status: WorkflowStatus
    tags: list[str] = []
    definition: dict[str, Any]  # Parsed workflow structure
    yaml: str  # Original YAML
    created_at: datetime
    updated_at: datetime


class WorkflowListResponse(BaseModel):
    """Paginated workflow list."""

    workflows: list[WorkflowSummary]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool
    has_prev: bool


class WorkflowCreateResponse(BaseModel):
    """Response after creating workflow."""

    id: str
    name: str
    version: str
    status: WorkflowStatus
    created_at: datetime


class ValidationError(BaseModel):
    """Single validation error."""

    path: str
    message: str
    line: int | None = None


class ValidationResult(BaseModel):
    """Workflow validation result."""

    valid: bool
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
