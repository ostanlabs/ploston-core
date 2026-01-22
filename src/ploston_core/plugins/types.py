"""Plugin framework types and context dataclasses.

This module defines the core types for the AEL plugin framework:
- PluginDecision: Enum for plugin hook decisions
- HookResult: Generic result wrapper for hook execution
- Context dataclasses: RequestContext, StepContext, StepResult, ResponseContext
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class PluginDecision(Enum):
    """Decision returned by plugin hooks.

    For OSS plugins, only CONTINUE is used.
    Premium plugins can use SKIP, ABORT, RETRY.
    """

    CONTINUE = "continue"  # Continue with (possibly modified) data
    # Premium-only decisions (reserved for future use)
    # SKIP = "skip"        # Skip this step/request
    # ABORT = "abort"      # Abort execution with error
    # RETRY = "retry"      # Retry the operation


@dataclass
class HookResult(Generic[T]):
    """Result of a plugin hook execution.

    Wraps the data returned by a hook with metadata about the execution.

    Attributes:
        data: The (possibly modified) data from the hook
        decision: The plugin's decision (CONTINUE for OSS)
        modified: Whether the plugin modified the data
        metadata: Optional metadata from the plugin
    """

    data: T
    decision: PluginDecision = PluginDecision.CONTINUE
    modified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unchanged(cls, data: T) -> "HookResult[T]":
        """Create a result indicating data was not modified."""
        return cls(data=data, modified=False)

    @classmethod
    def changed(cls, data: T, metadata: dict[str, Any] | None = None) -> "HookResult[T]":
        """Create a result indicating data was modified."""
        return cls(data=data, modified=True, metadata=metadata or {})


@dataclass
class RequestContext:
    """Context passed to on_request_received hook."""

    workflow_id: str
    inputs: dict[str, Any]
    execution_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    """Context passed to on_step_before hook."""

    workflow_id: str
    execution_id: str
    step_id: str
    step_type: str  # "tool" or "code"
    tool_name: str | None
    params: dict[str, Any]
    step_index: int = 0
    total_steps: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResultContext:
    """Context passed to on_step_after hook."""

    workflow_id: str
    execution_id: str
    step_id: str
    step_type: str
    tool_name: str | None
    params: dict[str, Any]
    output: Any
    success: bool
    error: Exception | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResponseContext:
    """Context passed to on_response_ready hook."""

    workflow_id: str
    execution_id: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    success: bool
    error: Exception | None = None
    duration_ms: int = 0
    step_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
