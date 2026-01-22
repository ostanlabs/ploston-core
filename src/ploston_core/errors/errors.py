"""AEL Error types and error registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Error source categories."""

    TOOL = "TOOL"
    EXECUTION = "EXECUTION"
    VALIDATION = "VALIDATION"
    WORKFLOW = "WORKFLOW"
    SYSTEM = "SYSTEM"


@dataclass
class AELError(Exception):
    """Structured error with context. Base exception for all AEL errors."""

    # Identity
    code: str  # e.g., "TOOL_UNAVAILABLE"
    category: ErrorCategory

    # Messages
    message: str  # Human-readable summary
    detail: str | None = None  # Extended explanation
    suggestion: str | None = None  # Actionable fix

    # Context
    retryable: bool = False  # Is retry potentially useful?
    http_status: int = 500  # For REST API responses
    step_id: str | None = None  # Which step failed
    tool_name: str | None = None  # Which tool failed
    execution_id: str | None = None  # Execution identifier

    # Error chain (max depth 3)
    cause: "AELError | None" = None

    # Metadata
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        """Set Exception message."""
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses.

        Returns:
            Dictionary representation of the error
        """
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "retryable": self.retryable,
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "execution_id": self.execution_id,
            "timestamp": self.timestamp.isoformat(),
            "cause": self.cause.to_dict() if self.cause else None,
        }

    def with_context(
        self,
        step_id: str | None = None,
        tool_name: str | None = None,
        execution_id: str | None = None,
    ) -> "AELError":
        """Return copy with additional context.

        Args:
            step_id: Optional step identifier
            tool_name: Optional tool name
            execution_id: Optional execution identifier

        Returns:
            New AELError instance with updated context
        """
        return AELError(
            code=self.code,
            category=self.category,
            message=self.message,
            detail=self.detail,
            suggestion=self.suggestion,
            retryable=self.retryable,
            http_status=self.http_status,
            step_id=step_id or self.step_id,
            tool_name=tool_name or self.tool_name,
            execution_id=execution_id or self.execution_id,
            cause=self.cause,
            timestamp=self.timestamp,
        )


@dataclass
class ErrorTemplate:
    """Template for creating errors."""

    code: str
    category: ErrorCategory
    message_template: str  # "Tool '{tool_name}' is unavailable"
    detail_template: str | None = None
    suggestion_template: str | None = None
    default_retryable: bool = False
    default_http_status: int = 500


@dataclass
class MatchResult:
    """Result of matching an exception."""

    ael_code: str
    context: dict[str, Any]
    retryable: bool | None = None  # None = use template default


class ErrorMatcher(ABC):
    """Base class for exception matchers."""

    @abstractmethod
    def matches(self, error: Exception | dict[str, Any]) -> bool:
        """Check if this matcher handles the error.

        Args:
            error: Exception or error dict to check

        Returns:
            True if this matcher can handle the error
        """

    @abstractmethod
    def extract(self, error: Exception | dict[str, Any]) -> MatchResult:
        """Extract AEL error info from the exception.

        Args:
            error: Exception or error dict to extract from

        Returns:
            MatchResult with error code and context
        """
