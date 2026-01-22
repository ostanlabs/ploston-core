"""Template context builder."""

from typing import Any

from ploston_core.types import StepOutput

from .types import TemplateContext


class ContextBuilder:
    """Build TemplateContext incrementally during workflow execution."""

    def __init__(
        self,
        inputs: dict[str, Any],
        config: dict[str, Any],
        execution_id: str,
    ):
        """Initialize context builder.

        Args:
            inputs: Workflow inputs
            config: Workflow config
            execution_id: Execution ID
        """
        self._context = TemplateContext(
            inputs=inputs,
            steps={},
            config=config,
            execution_id=execution_id,
        )

    def add_step_output(
        self,
        step_id: str,
        output: Any,
        success: bool,
        duration_ms: int,
    ) -> None:
        """Add a completed step's output to context.

        Args:
            step_id: Step identifier
            output: Step output value
            success: Whether step succeeded
            duration_ms: Step duration in milliseconds
        """
        self._context.steps[step_id] = StepOutput(
            output=output,
            success=success,
            duration_ms=duration_ms,
            step_id=step_id,
        )

    def get_context(self) -> TemplateContext:
        """Get current context.

        Returns:
            Current template context
        """
        return self._context
