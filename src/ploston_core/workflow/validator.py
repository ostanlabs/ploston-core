"""Workflow validation."""

from typing import TYPE_CHECKING

from ploston_core.template import TemplateEngine
from ploston_core.types import ValidationIssue, ValidationResult

from .types import WorkflowDefinition

if TYPE_CHECKING:
    from ploston_core.registry import ToolRegistry


class WorkflowValidator:
    """Validate workflow definitions."""

    def __init__(self, tool_registry: "ToolRegistry"):
        """Initialize validator.

        Args:
            tool_registry: Tool registry for tool existence checks
        """
        self._tool_registry = tool_registry
        self._template_engine = TemplateEngine()

    def validate(
        self,
        workflow: WorkflowDefinition,
        check_tools: bool = True,
    ) -> ValidationResult:
        """Validate workflow definition.

        Checks:
        - Required fields present
        - Valid types
        - Unique step IDs
        - Valid depends_on references
        - No circular dependencies
        - Tool XOR code per step
        - Tool exists (if check_tools=True)
        - Valid template syntax in params

        Args:
            workflow: Workflow to validate
            check_tools: Whether to check tool existence

        Returns:
            ValidationResult with errors and warnings
        """
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        # Check required fields
        if not workflow.name:
            errors.append(
                ValidationIssue(
                    path="name",
                    message="Workflow name is required",
                    severity="error",
                )
            )

        if not workflow.version:
            errors.append(
                ValidationIssue(
                    path="version",
                    message="Workflow version is required",
                    severity="error",
                )
            )

        # Check unique step IDs
        step_ids = [step.id for step in workflow.steps]
        duplicates = [sid for sid in step_ids if step_ids.count(sid) > 1]
        if duplicates:
            errors.append(
                ValidationIssue(
                    path="steps",
                    message=f"Duplicate step IDs: {', '.join(set(duplicates))}",
                    severity="error",
                )
            )

        # Validate each step
        for step in workflow.steps:
            # Tool XOR code
            if step.tool and step.code:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}",
                        message="Step must have either 'tool' or 'code', not both",
                        severity="error",
                    )
                )
            elif not step.tool and not step.code:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}",
                        message="Step must have either 'tool' or 'code'",
                        severity="error",
                    )
                )

            # Check tool exists
            if check_tools and step.tool:
                tool = self._tool_registry.get(step.tool)
                if not tool:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.tool",
                            message=f"Tool '{step.tool}' not found in registry",
                            severity="error",
                        )
                    )

            # Validate depends_on references
            if step.depends_on:
                for dep in step.depends_on:
                    if dep not in step_ids:
                        errors.append(
                            ValidationIssue(
                                path=f"steps.{step.id}.depends_on",
                                message=f"Dependency '{dep}' not found",
                                severity="error",
                            )
                        )

            # Validate template syntax in params
            if step.params:
                template_errors = self._template_engine.validate(step.params)
                for error in template_errors:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.params",
                            message=f"Template error: {error}",
                            severity="error",
                        )
                    )

        # Check for circular dependencies
        try:
            workflow.get_execution_order()
        except ValueError as e:
            errors.append(
                ValidationIssue(
                    path="steps",
                    message=str(e),
                    severity="error",
                )
            )

        # Validate outputs
        for output in workflow.outputs:
            if output.from_path and output.value:
                errors.append(
                    ValidationIssue(
                        path=f"outputs.{output.name}",
                        message="Output must have either 'from_path' or 'value', not both",
                        severity="error",
                    )
                )
            elif not output.from_path and not output.value:
                errors.append(
                    ValidationIssue(
                        path=f"outputs.{output.name}",
                        message="Output must have either 'from_path' or 'value'",
                        severity="error",
                    )
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
