"""Template Engine type definitions."""

from dataclasses import dataclass, field
from typing import Any

from ploston_core.types import StepOutput


@dataclass
class TemplateContext:
    """Context available to templates.

    Access patterns:
    - {{ inputs.url }} → self.inputs["url"]
    - {{ steps.fetch.output }} → self.steps["fetch"].output
    - {{ config.timeout }} → self.config["timeout"]
    """

    inputs: dict[str, Any]  # Workflow inputs
    steps: dict[str, StepOutput]  # Previous step outputs (wrapped)
    config: dict[str, Any]  # Workflow config
    execution_id: str  # Current execution ID


@dataclass
class RenderResult:
    """Result of template rendering."""

    value: Any  # Rendered value
    had_templates: bool  # Whether any templates were found
    templates_rendered: list[str] = field(default_factory=list)  # Template expressions found
