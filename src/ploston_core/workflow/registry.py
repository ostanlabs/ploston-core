"""Workflow Registry implementation."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ploston_core.errors import create_error
from ploston_core.types import LogLevel, ValidationResult

from .parser import parse_workflow_yaml
from .types import WorkflowDefinition, WorkflowEntry
from .validator import WorkflowValidator

if TYPE_CHECKING:
    from ploston_core.config import WorkflowsConfig
    from ploston_core.logging import AELLogger
    from ploston_core.registry import ToolRegistry


class WorkflowRegistry:
    """Registry of workflow definitions.

    Loads workflows from directory, validates them,
    and provides hot-reload on file changes.
    """

    def __init__(
        self,
        tool_registry: "ToolRegistry",
        config: "WorkflowsConfig",
        logger: "AELLogger | None" = None,
    ):
        """Initialize workflow registry.

        Args:
            tool_registry: Tool registry for validation
            config: Workflows configuration
            logger: Optional logger
        """
        self._workflows: dict[str, WorkflowEntry] = {}
        self._tool_registry = tool_registry
        self._config = config
        self._logger = logger
        self._validator = WorkflowValidator(tool_registry)
        self._watching = False
        self._watch_task: asyncio.Task[None] | None = None

    async def initialize(self) -> int:
        """Initialize registry by loading workflows from directory.

        Returns:
            Number of workflows loaded
        """
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Initializing workflow registry",
                {"directory": str(self._config.directory)},
            )

        workflows_dir = Path(self._config.directory)
        if not workflows_dir.exists():
            if self._logger:
                self._logger._log(
                    LogLevel.WARN,
                    "workflow",
                    "Workflows directory does not exist",
                    {"directory": str(workflows_dir)},
                )
            return 0

        count = 0
        for yaml_file in workflows_dir.glob("*.yaml"):
            try:
                yaml_content = yaml_file.read_text()
                self.register_from_yaml(yaml_content, source_path=yaml_file)
                count += 1
            except Exception as e:
                if self._logger:
                    self._logger._log(
                        LogLevel.ERROR,
                        "workflow",
                        "Failed to load workflow",
                        {"file": str(yaml_file), "error": str(e)},
                    )

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow registry initialized",
                {"count": count},
            )

        return count

    def register(
        self,
        workflow: WorkflowDefinition,
        validate: bool = True,
    ) -> ValidationResult:
        """Register a workflow.

        Args:
            workflow: Workflow to register
            validate: Whether to validate before registering

        Returns:
            ValidationResult (always valid if validate=False)

        Raises:
            AELError(INPUT_INVALID) if validation fails
        """
        if validate:
            result = self._validator.validate(workflow)
            if not result.valid:
                error_msgs = [f"{e.path}: {e.message}" for e in result.errors]
                raise create_error(
                    "INPUT_INVALID",
                    detail="; ".join(error_msgs),
                )
        else:
            result = ValidationResult(valid=True, errors=[], warnings=[])

        entry = WorkflowEntry(
            workflow=workflow,
            registered_at=datetime.now(UTC).isoformat(),
            source="api",
        )
        self._workflows[workflow.name] = entry

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow registered",
                {"name": workflow.name},
            )

        return result

    def register_from_yaml(
        self,
        yaml_content: str,
        source_path: Path | None = None,
    ) -> ValidationResult:
        """Parse and register workflow from YAML.

        Args:
            yaml_content: YAML content
            source_path: Optional source file path

        Returns:
            ValidationResult

        Raises:
            AELError(INPUT_INVALID) if parsing or validation fails
        """
        workflow = parse_workflow_yaml(yaml_content, source_path)
        result = self.register(workflow, validate=True)

        # Update source in entry
        if workflow.name in self._workflows:
            self._workflows[workflow.name].source = "file" if source_path else "api"

        return result

    def unregister(self, name: str) -> bool:
        """Unregister a workflow.

        Args:
            name: Workflow name

        Returns:
            True if workflow was registered, False if not found
        """
        if name in self._workflows:
            del self._workflows[name]
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "workflow",
                    "Workflow unregistered",
                    {"name": name},
                )
            return True
        return False

    def get(self, name: str) -> WorkflowDefinition | None:
        """Get workflow by name.

        Args:
            name: Workflow name

        Returns:
            Workflow definition or None if not found
        """
        entry = self._workflows.get(name)
        return entry.workflow if entry else None

    def get_or_raise(self, name: str) -> WorkflowDefinition:
        """Get workflow by name, raise if not found.

        Args:
            name: Workflow name

        Returns:
            Workflow definition

        Raises:
            AELError(WORKFLOW_NOT_FOUND)
        """
        workflow = self.get(name)
        if not workflow:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)
        return workflow

    def list_workflows(self) -> list[WorkflowDefinition]:
        """List all registered workflows.

        Returns:
            List of workflow definitions
        """
        return [entry.workflow for entry in self._workflows.values()]

    def validate_yaml(self, yaml_content: str) -> ValidationResult:
        """Validate YAML without registering.

        Useful for CLI `ael validate` command.

        Args:
            yaml_content: YAML content to validate

        Returns:
            ValidationResult
        """
        try:
            workflow = parse_workflow_yaml(yaml_content)
            return self._validator.validate(workflow)
        except Exception as e:
            from ploston_core.types import ValidationIssue

            return ValidationResult(
                valid=False,
                errors=[
                    ValidationIssue(
                        path="yaml",
                        message=str(e),
                        severity="error",
                    )
                ],
                warnings=[],
            )

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Get workflows formatted as MCP tools.

        Returns list of tool definitions with workflow: prefix.

        Returns:
            List of MCP tool definitions
        """
        tools: list[dict[str, Any]] = []

        for workflow in self.list_workflows():
            # Build input schema
            properties: dict[str, Any] = {}
            required: list[str] = []

            for inp in workflow.inputs:
                prop: dict[str, Any] = {"type": inp.type}
                if inp.description:
                    prop["description"] = inp.description
                if inp.enum:
                    prop["enum"] = inp.enum
                if inp.pattern:
                    prop["pattern"] = inp.pattern
                if inp.minimum is not None:
                    prop["minimum"] = inp.minimum
                if inp.maximum is not None:
                    prop["maximum"] = inp.maximum

                properties[inp.name] = prop
                if inp.required:
                    required.append(inp.name)

            input_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                input_schema["required"] = required

            # Create tool definition
            tool = {
                "name": f"workflow:{workflow.name}",
                "description": workflow.description or f"Execute {workflow.name} workflow",
                "inputSchema": input_schema,
            }
            tools.append(tool)

        return tools

    def snapshot(self, name: str) -> dict[str, Any]:
        """Get workflow snapshot for execution.

        Returns frozen copy of workflow definition.

        Args:
            name: Workflow name

        Returns:
            Workflow snapshot as dict

        Raises:
            AELError(WORKFLOW_NOT_FOUND)
        """
        workflow = self.get_or_raise(name)

        # Return a dict representation (frozen copy)
        return {
            "name": workflow.name,
            "version": workflow.version,
            "description": workflow.description,
            "packages": {
                "profile": workflow.packages.profile if workflow.packages else "standard",
                "additional": workflow.packages.additional if workflow.packages else [],
            },
            "defaults": {
                "timeout": workflow.defaults.timeout if workflow.defaults else 30,
                "on_error": workflow.defaults.on_error.value if workflow.defaults else "fail",
                "retry": (
                    {
                        "max_attempts": workflow.defaults.retry.max_attempts,
                        "backoff": workflow.defaults.retry.backoff.value,
                        "delay_seconds": workflow.defaults.retry.delay_seconds,
                    }
                    if workflow.defaults and workflow.defaults.retry
                    else None
                ),
            },
            "inputs": [
                {
                    "name": inp.name,
                    "type": inp.type,
                    "required": inp.required,
                    "default": inp.default,
                    "description": inp.description,
                }
                for inp in workflow.inputs
            ],
            "steps": [
                {
                    "id": step.id,
                    "tool": step.tool,
                    "code": step.code,
                    "params": step.params,
                    "depends_on": step.depends_on,
                    "on_error": step.on_error.value if step.on_error else None,
                    "timeout": step.timeout,
                    "retry": (
                        {
                            "max_attempts": step.retry.max_attempts,
                            "backoff": step.retry.backoff.value,
                            "delay_seconds": step.retry.delay_seconds,
                        }
                        if step.retry
                        else None
                    ),
                }
                for step in workflow.steps
            ],
            "outputs": [
                {
                    "name": out.name,
                    "from_path": out.from_path,
                    "value": out.value,
                    "description": out.description,
                }
                for out in workflow.outputs
            ],
        }

    def start_watching(self) -> None:
        """Start file watcher for hot-reload."""
        if self._watching:
            return

        self._watching = True
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Starting workflow file watcher",
                {},
            )

        # Note: Actual file watching implementation would go here
        # For now, this is a placeholder

    def stop_watching(self) -> None:
        """Stop file watcher."""
        if not self._watching:
            return

        self._watching = False
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Stopped workflow file watcher",
                {},
            )

    async def _on_file_change(self, path: Path) -> None:
        """Handle workflow file change.

        Args:
            path: Path to changed file
        """
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow file changed",
                {"file": str(path)},
            )

        try:
            yaml_content = path.read_text()
            self.register_from_yaml(yaml_content, source_path=path)
        except Exception as e:
            if self._logger:
                self._logger._log(
                    LogLevel.ERROR,
                    "workflow",
                    "Failed to reload workflow",
                    {"file": str(path), "error": str(e)},
                )
