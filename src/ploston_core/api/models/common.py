"""Common REST API models."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────────


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046
    """Paginated list response."""

    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool
    has_prev: bool


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# ─────────────────────────────────────────────────────────────────
# Error Response
# ─────────────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    """Error detail model (matches Error Registry)."""

    code: str
    category: str
    message: str
    detail: str | None = None
    suggestion: str | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: ErrorDetail


# ─────────────────────────────────────────────────────────────────
# Health & Info
# ─────────────────────────────────────────────────────────────────


class HealthStatus(str, Enum):
    """Health status enum."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


class HealthCheck(BaseModel):
    """Health check response."""

    status: HealthStatus
    checks: dict[str, str]
    timestamp: datetime


class ServerInfo(BaseModel):
    """Server info response."""

    name: str = "AEL"
    version: str
    edition: str = "oss"
    features: dict[str, bool]
    mcp: dict[str, Any]
