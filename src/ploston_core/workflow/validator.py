"""Workflow validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ploston_core.template import TemplateEngine
from ploston_core.types import ValidationIssue, ValidationResult

from .types import StepDefinition, WorkflowDefinition

if TYPE_CHECKING:
    from ploston_core.registry import ToolRegistry
    from ploston_core.runner_management.registry import RunnerRegistry


class WorkflowValidator:
    """Validate workflow definitions."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        runner_registry: RunnerRegistry | None = None,
    ):
        """Initialize validator.

        Args:
            tool_registry: Tool registry for CP-direct tool existence checks
            runner_registry: Optional runner registry for runner-hosted tool checks
        """
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
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

            # Check mcp is set for tool steps
            if step.tool and not step.mcp:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}.mcp",
                        message=(
                            f"Tool step '{step.id}' is missing the 'mcp' field. "
                            "The 'mcp' field is required for tool steps — it identifies "
                            "which MCP server hosts the tool."
                        ),
                        severity="error",
                    )
                )

            # Check tool exists via (mcp, tool, runner) resolution
            if check_tools and step.tool and step.mcp:
                resolved, resolve_error = self._resolve_tool(
                    step, workflow.defaults.runner if workflow.defaults else None
                )
                if not resolved:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.tool",
                            message=resolve_error or f"Tool '{step.tool}' not found",
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

            # Validate when expression syntax (must be a valid Jinja2 expression)
            if step.when:
                when_errors = self._template_engine.validate({"_when": "{{ " + step.when + " }}"})
                for error in when_errors:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.when",
                            message=f"Template error in when expression: {error}",
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

    # ── Tool resolution ──────────────────────────────────────────

    def _resolve_tool(
        self,
        step: StepDefinition,
        default_runner: str | None,
    ) -> tuple[bool, str | None]:
        """Resolve a tool step to verify the tool exists.

        Resolution chain (per WORKFLOW_TOOL_RESOLUTION_SPEC DEC-157):
        1. Determine runner: default_runner (from workflow defaults)
           or bridge context (handled at execution time — skipped here).
        2. If runner found: check {runner}__{mcp}__{tool} on runner.
        3. If no runner: check tool by (server_name, name) on CP-direct registry.
        4. If not found: return error with available tools hint.

        Returns:
            (True, None) if found, (False, error_message) if not.
        """
        assert step.tool is not None
        assert step.mcp is not None

        # Determine effective runner: explicit default > inference from registry
        runner = default_runner

        # Path 1: Runner inference — if no explicit runner, check if exactly
        # one connected runner hosts this MCP server (spec step 3, DEC-157).
        if runner is None and self._runner_registry:
            matching_runners = []
            for r in self._runner_registry.list():
                if r.status.value != "connected":
                    continue
                for tool_entry in r.available_tools:
                    name = self._runner_registry._get_tool_name(tool_entry)
                    if name.startswith(f"{step.mcp}__"):
                        matching_runners.append(r)
                        break
            if len(matching_runners) == 1:
                runner = matching_runners[0].name
            elif len(matching_runners) > 1:
                names = sorted(r.name for r in matching_runners)
                return False, (
                    f"MCP server '{step.mcp}' is hosted on multiple runners: {names}. "
                    f"Add 'defaults.runner' to the workflow to disambiguate."
                )

        # Path 2: Runner-hosted tool lookup (explicit or inferred runner)
        if runner and self._runner_registry:
            canonical = f"{runner}__{step.mcp}__{step.tool}"
            if self._runner_registry.has_tool(runner, canonical):
                return True, None
            # Build hint
            runner_obj = self._runner_registry.get_by_name(runner)
            if runner_obj:
                avail = [
                    self._runner_registry._get_tool_name(t)
                    for t in runner_obj.available_tools
                    if self._runner_registry._get_tool_name(t).startswith(f"{step.mcp}__")
                ]
                hint = (
                    f" Available tools on '{step.mcp}' server (runner '{runner}'): {avail}"
                    if avail
                    else ""
                )
            else:
                hint = f" Runner '{runner}' not found in registry."
            return False, (
                f"Tool '{step.tool}' not found on MCP server '{step.mcp}' "
                f"(runner '{runner}').{hint}"
            )

        # Path 3: CP-direct tool — match by (server_name, tool name)
        matching = self._tool_registry.list_tools(server_name=step.mcp)
        for tool_def in matching:
            if tool_def.name == step.tool:
                return True, None

        # Build hint for CP-direct
        avail_names = [t.name for t in matching] if matching else []
        if avail_names:
            hint = f" Available tools on '{step.mcp}' server: {avail_names}"
        else:
            # List all known server names
            all_tools = self._tool_registry.list_tools()
            servers = sorted({t.server_name for t in all_tools if t.server_name})
            hint = f" No MCP server named '{step.mcp}' found. Known servers: {servers}"
        return False, (f"Tool '{step.tool}' not found on MCP server '{step.mcp}'.{hint}")
