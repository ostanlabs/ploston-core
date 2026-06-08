"""YAML workflow parsing."""

import logging
from pathlib import Path
from typing import Any

import yaml

from ploston_core.errors import create_error
from ploston_core.types import BackoffType, OnError, OnMissingTool, RetryConfig

from .types import (
    InputDefinition,
    OutputDefinition,
    PackagesConfig,
    StepDefinition,
    WorkflowDefaults,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)


def parse_workflow_yaml(
    yaml_content: str,
    source_path: Path | None = None,
) -> WorkflowDefinition:
    """Parse YAML content into WorkflowDefinition.

    Handles both lightweight and full input syntax.

    Args:
        yaml_content: YAML content to parse
        source_path: Optional source file path

    Returns:
        Parsed workflow definition

    Raises:
        AELError(INPUT_INVALID) if YAML is invalid
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise create_error("INPUT_INVALID", detail=f"Invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise create_error("INPUT_INVALID", detail="YAML must be a dictionary")

    # Parse metadata
    workflow_name = data.get("name")
    if not workflow_name:
        raise create_error("INPUT_INVALID", detail="Workflow name is required")

    version = data.get("version", "1.0.0")
    description = data.get("description")

    # Parse packages config.
    #
    # DEPRECATED: `packages.profile` / `packages.additional` are still parsed so
    # existing configs (e.g. ploston-config.yaml shipping `default_profile:
    # standard`) keep loading, but they have NO runtime effect — the sandbox
    # import allowlist is fixed and does not consult these fields. We keep
    # parsing them and emit a deprecation warning instead of dropping them.
    packages = None
    if "packages" in data:
        pkg_data = data["packages"]
        packages = PackagesConfig(
            profile=pkg_data.get("profile", "standard"),
            additional=pkg_data.get("additional", []),
        )
        logger.warning(
            "Workflow %r declares a `packages` block (profile=%r, additional=%r), "
            "but `packages.profile`/`packages.additional` are DEPRECATED and have "
            "no runtime effect: the sandbox import allowlist is fixed and ignores "
            "these fields. They are parsed only for backward compatibility and may "
            "be removed in a future release.",
            workflow_name,
            packages.profile,
            packages.additional,
        )

    # Parse defaults
    defaults = None
    if "defaults" in data:
        def_data = data["defaults"]
        retry = None
        if "retry" in def_data:
            retry_data = def_data["retry"]
            retry = RetryConfig(
                max_attempts=retry_data.get("max_attempts", 3),
                backoff=BackoffType(retry_data.get("backoff", "fixed")),
                delay_seconds=retry_data.get("delay_seconds", 1.0),
            )

        defaults = WorkflowDefaults(
            timeout=def_data.get("timeout", 30),
            on_error=OnError(def_data.get("on_error", "fail")),
            retry=retry,
            runner=def_data.get("runner"),
        )

    # Parse inputs
    inputs = normalize_inputs(data.get("inputs", []))

    # Parse steps
    steps: list[StepDefinition] = []
    for step_data in data.get("steps", []):
        retry = None
        if "retry" in step_data:
            retry_data = step_data["retry"]
            retry = RetryConfig(
                max_attempts=retry_data.get("max_attempts", 3),
                backoff=BackoffType(retry_data.get("backoff", "fixed")),
                delay_seconds=retry_data.get("delay_seconds", 1.0),
            )

        on_missing_tool_raw = step_data.get("on_missing_tool")
        on_missing_tool = OnMissingTool(on_missing_tool_raw) if on_missing_tool_raw else None

        step = StepDefinition(
            id=step_data["id"],
            tool=step_data.get("tool"),
            code=step_data.get("code"),
            mcp=step_data.get("mcp"),
            params=step_data.get("params", {}),
            depends_on=step_data.get("depends_on"),
            on_error=OnError(step_data["on_error"]) if "on_error" in step_data else None,
            timeout=step_data.get("timeout"),
            retry=retry,
            on_missing_tool=on_missing_tool,
            when=step_data.get("when"),
        )
        steps.append(step)

    # Parse outputs - handle both list and dict formats
    outputs: list[OutputDefinition] = []
    raw_outputs = data.get("outputs", [])
    if isinstance(raw_outputs, dict):
        # Dict format: {output_name: {from: ..., value: ...}}
        for output_name, output_data in raw_outputs.items():
            if isinstance(output_data, dict):
                output = OutputDefinition(
                    name=output_name,
                    from_path=output_data.get("from") or output_data.get("from_path"),
                    value=output_data.get("value"),
                    description=output_data.get("description"),
                )
            else:
                # Simple value
                output = OutputDefinition(name=output_name, value=output_data)
            outputs.append(output)
    else:
        # List format: [{name: ..., from: ...}] or [{name: ..., from_path: ...}]
        for output_data in raw_outputs:
            output = OutputDefinition(
                name=output_data["name"],
                from_path=output_data.get("from") or output_data.get("from_path"),
                value=output_data.get("value"),
                description=output_data.get("description"),
            )
            outputs.append(output)

    return WorkflowDefinition(
        name=workflow_name,
        version=version,
        description=description,
        packages=packages,
        defaults=defaults,
        inputs=inputs,
        steps=steps,
        outputs=outputs,
        source_path=source_path,
        yaml_content=yaml_content,
    )


def normalize_inputs(raw_inputs: Any) -> list[InputDefinition]:
    """Normalize input definitions.

    Handles:
    - ["name"]  →  InputDefinition(name="name", required=True)
    - ["name": default]  →  InputDefinition(name="name", default=default)
    - {name: {type: ..., ...}}  →  Full InputDefinition

    Args:
        raw_inputs: Raw input data from YAML

    Returns:
        List of normalized input definitions
    """
    if not raw_inputs:
        return []

    inputs: list[InputDefinition] = []

    for item in raw_inputs:
        if isinstance(item, str):
            # Simple string: required input
            inputs.append(InputDefinition(name=item, required=True))
        elif isinstance(item, dict) and "name" in item and not isinstance(item["name"], dict):
            # Record form: {name: "url", type: ..., required: ..., ...}
            # Exactly one InputDefinition, named by item["name"].
            inputs.append(
                InputDefinition(
                    name=item["name"],
                    type=item.get("type", "string"),
                    required=item.get("required", "default" not in item),
                    default=item.get("default"),
                    description=item.get("description"),
                    enum=item.get("enum"),
                    pattern=item.get("pattern"),
                    minimum=item.get("minimum"),
                    maximum=item.get("maximum"),
                )
            )
        elif isinstance(item, dict):
            # Map form: {name: {type: ..., ...}} or {name: scalar_default}.
            # One InputDefinition per key (escape hatch: {name: {...}} -> input
            # literally named "name").
            for name, value in item.items():
                if isinstance(value, dict):
                    # Full definition
                    inputs.append(
                        InputDefinition(
                            name=name,
                            type=value.get("type", "string"),
                            required=value.get("required", True),
                            default=value.get("default"),
                            description=value.get("description"),
                            enum=value.get("enum"),
                            pattern=value.get("pattern"),
                            minimum=value.get("minimum"),
                            maximum=value.get("maximum"),
                        )
                    )
                else:
                    # Simple default value
                    inputs.append(
                        InputDefinition(
                            name=name,
                            required=False,
                            default=value,
                        )
                    )

    return inputs
