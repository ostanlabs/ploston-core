"""Shared execution types for AEL."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class StepOutput:
    """Output from a completed workflow step.

    Used by:
    - TemplateEngine (TemplateContext.steps)
    - PythonExecSandbox (SandboxContext.steps)
    - WorkflowEngine (building context)

    Access pattern in templates: {{ steps.fetch.output }}
    Access pattern in code: context.steps['fetch'].output
    """

    output: Any  # The actual step result
    success: bool  # Whether step succeeded
    duration_ms: int  # Execution time
    step_id: str  # Step identifier


@dataclass
class ToolCallContext:
    """Context for a tool call (for logging/tracing)."""

    step_id: str | None = None
    execution_id: str | None = None
    workflow_id: str | None = None


class ToolCallerProtocol(Protocol):
    """Protocol for tool calling interface.

    Used by:
    - PythonExecSandbox (for calling tools from code)
    - WorkflowEngine (for executing tool steps)
    """

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool with given arguments.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dictionary

        Returns:
            Tool execution result
        """
        ...
