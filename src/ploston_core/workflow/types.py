"""Workflow data model types."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ploston_core.types import OnError, RetryConfig, StepType


@dataclass
class WorkflowDefaults:
    """Default settings for workflow steps."""

    timeout: int = 30
    on_error: OnError = OnError.FAIL
    retry: RetryConfig | None = None


@dataclass
class InputDefinition:
    """Workflow input definition."""

    name: str
    type: str = "string"
    required: bool = True
    default: Any = None
    description: str | None = None
    # JSON Schema validation fields
    enum: list[Any] | None = None
    pattern: str | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class OutputDefinition:
    """Workflow output definition."""

    name: str
    from_path: str | None = None  # "steps.transform.output.items"
    value: str | None = None  # "{{ expression }}"
    description: str | None = None


@dataclass
class StepDefinition:
    """Workflow step definition."""

    id: str

    # Type (tool XOR code)
    tool: str | None = None
    code: str | None = None

    # Parameters (for tool steps)
    params: dict[str, Any] = field(default_factory=dict)

    # Dependencies
    depends_on: list[str] | None = None  # Explicit dependencies

    # Error handling
    on_error: OnError | None = None
    timeout: int | None = None
    retry: RetryConfig | None = None

    @property
    def step_type(self) -> StepType:
        """Get step type (TOOL or CODE)."""
        return StepType.TOOL if self.tool else StepType.CODE


@dataclass
class PackagesConfig:
    """Python packages configuration for code steps."""

    profile: str = "standard"
    additional: list[str] = field(default_factory=list)


@dataclass
class WorkflowDefinition:
    """Complete workflow definition."""

    # Metadata
    name: str
    version: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)

    # Configuration
    packages: PackagesConfig | None = None
    defaults: WorkflowDefaults | None = None

    # Schema
    inputs: list[InputDefinition] = field(default_factory=list)
    steps: list[StepDefinition] = field(default_factory=list)
    outputs: list[OutputDefinition] = field(default_factory=list)

    # Source
    source_path: Path | None = None
    yaml_content: str | None = None

    def get_step(self, step_id: str) -> StepDefinition | None:
        """Get step by ID.

        Args:
            step_id: Step identifier

        Returns:
            Step definition or None if not found
        """
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_execution_order(self) -> list[str]:
        """Get steps in execution order (topological sort).

        Respects depends_on, defaults to sequential (YAML order).

        Returns:
            List of step IDs in execution order

        Raises:
            ValueError if circular dependency detected
        """
        # Build step index for preserving YAML order
        step_index = {step.id: i for i, step in enumerate(self.steps)}

        # Build dependency graph
        # graph[step_id] = list of steps that step_id depends on
        graph: dict[str, list[str]] = {}
        in_degree: dict[str, int] = {}

        for step in self.steps:
            graph[step.id] = step.depends_on or []
            in_degree[step.id] = 0

        # Calculate in-degrees (number of dependencies each step has)
        for step in self.steps:
            # Each dependency adds 1 to this step's in-degree
            in_degree[step.id] = len(graph[step.id])

        # Topological sort (Kahn's algorithm)
        # Start with steps that have no dependencies (in_degree == 0)
        queue = [step_id for step_id, degree in in_degree.items() if degree == 0]
        result: list[str] = []

        while queue:
            # Sort by original YAML order to preserve sequential execution
            # when there are no explicit dependencies
            queue.sort(key=lambda x: step_index[x])
            current = queue.pop(0)
            result.append(current)

            # For each step that depends on current, decrement its in-degree
            for step in self.steps:
                if current in graph[step.id]:
                    in_degree[step.id] -= 1
                    if in_degree[step.id] == 0:
                        queue.append(step.id)

        if len(result) != len(self.steps):
            raise ValueError("Circular dependency detected in workflow")

        return result

    def get_input_schema(self) -> dict[str, Any]:
        """Generate JSON Schema for inputs (for MCP exposure).

        Returns:
            JSON Schema dict
        """
        properties: dict[str, Any] = {}
        required: list[str] = []

        for inp in self.inputs:
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
            if inp.default is not None:
                prop["default"] = inp.default

            properties[inp.name] = prop
            if inp.required:
                required.append(inp.name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required

        return schema

    def get_output_schema(self) -> dict[str, Any]:
        """Generate JSON Schema for outputs (for MCP exposure).

        Returns:
            JSON Schema dict
        """
        properties: dict[str, Any] = {}

        for out in self.outputs:
            prop: dict[str, Any] = {"type": "string"}  # Default to string
            if out.description:
                prop["description"] = out.description

            properties[out.name] = prop

        return {
            "type": "object",
            "properties": properties,
        }


@dataclass
class WorkflowEntry:
    """Entry in workflow registry."""

    workflow: WorkflowDefinition
    registered_at: str
    source: str  # "file" | "api"
