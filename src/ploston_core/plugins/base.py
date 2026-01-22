"""AEL Plugin base class.

This module defines the AELPlugin base class that all plugins must inherit from.
Plugins can override hook methods to intercept and modify workflow execution.
"""

from typing import Any

from .types import (
    HookResult,
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)


class AELPlugin:
    """Base class for AEL plugins.

    Plugins extend this class and override hook methods to intercept
    workflow execution at various points.

    Hook execution order:
    1. on_request_received - When a workflow execution request arrives
    2. on_step_before - Before each step executes
    3. on_step_after - After each step completes
    4. on_response_ready - Before returning the final response

    Attributes:
        name: Plugin name (set from config)
        priority: Execution priority (lower = earlier, default 50)
        fail_open: If True, errors don't abort execution (default True)
        config: Plugin-specific configuration
    """

    name: str = "base"
    priority: int = 50
    fail_open: bool = True

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize the plugin.

        Args:
            config: Plugin-specific configuration from ael-config.yaml
        """
        self.config = config or {}

    def on_request_received(
        self, context: RequestContext
    ) -> RequestContext | HookResult[RequestContext]:
        """Called when a workflow execution request is received.

        Override this method to inspect or modify the request before execution.

        Args:
            context: Request context with workflow_id, inputs, etc.

        Returns:
            Modified context or HookResult wrapping the context.
            Return context unchanged to pass through.
        """
        return context

    def on_step_before(
        self, context: StepContext
    ) -> StepContext | HookResult[StepContext]:
        """Called before each step executes.

        Override this method to inspect or modify step parameters.

        Args:
            context: Step context with step_id, params, etc.

        Returns:
            Modified context or HookResult wrapping the context.
            Return context unchanged to pass through.
        """
        return context

    def on_step_after(
        self, context: StepResultContext
    ) -> StepResultContext | HookResult[StepResultContext]:
        """Called after each step completes.

        Override this method to inspect or modify step results.

        Args:
            context: Step result context with output, success, etc.

        Returns:
            Modified context or HookResult wrapping the context.
            Return context unchanged to pass through.
        """
        return context

    def on_response_ready(
        self, context: ResponseContext
    ) -> ResponseContext | HookResult[ResponseContext]:
        """Called before returning the final response.

        Override this method to inspect or modify the final response.

        Args:
            context: Response context with outputs, success, etc.

        Returns:
            Modified context or HookResult wrapping the context.
            Return context unchanged to pass through.
        """
        return context

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, priority={self.priority})"
