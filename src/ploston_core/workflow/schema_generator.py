"""Workflow schema generator.

Generates a JSON Schema description of the workflow YAML format
by introspecting the actual dataclass definitions. This ensures
the schema is always in sync with the parser (single source of truth).
"""

import dataclasses
import enum
import types
import typing
from typing import Any, get_type_hints

from .types import (
    InputDefinition,
    OutputDefinition,
    PackagesConfig,
    StepDefinition,
    WorkflowDefaults,
    WorkflowDefinition,
)

# Fields that are internal (not part of the YAML surface)
_INTERNAL_FIELDS = {"source_path", "yaml_content"}


def _python_type_to_json_schema(type_hint: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    # Handle None / NoneType
    if type_hint is type(None):
        return {"type": "null"}

    # Unwrap Optional[X] (Union[X, None])
    origin = typing.get_origin(type_hint)
    args = typing.get_args(type_hint)

    if origin is types.UnionType or origin is typing.Union:
        # Filter out NoneType
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            schema = _python_type_to_json_schema(non_none[0])
            schema["nullable"] = True
            return schema
        # Multi-type union (rare) — just mark as any
        return {}

    # Handle list[X]
    if origin is list:
        item_schema = _python_type_to_json_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}

    # Handle dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Handle enums
    if isinstance(type_hint, type) and issubclass(type_hint, enum.Enum):
        return {
            "type": "string",
            "enum": [e.value for e in type_hint],
        }

    # Handle dataclasses (nested)
    if dataclasses.is_dataclass(type_hint) and isinstance(type_hint, type):
        return _dataclass_to_schema(type_hint)

    # Primitives
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    if type_hint in type_map:
        return {"type": type_map[type_hint]}

    # Fallback
    return {}


def _dataclass_to_schema(cls: type) -> dict[str, Any]:
    """Convert a dataclass to a JSON Schema object."""
    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for f in dataclasses.fields(cls):
        type_hint = hints.get(f.name, str)
        prop = _python_type_to_json_schema(type_hint)

        # Add default info
        if f.default is not dataclasses.MISSING:
            if isinstance(f.default, enum.Enum):
                prop["default"] = f.default.value
            else:
                prop["default"] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            # Has a factory default — not required
            pass
        else:
            # No default — required
            origin = typing.get_origin(type_hint)
            args = typing.get_args(type_hint)
            is_optional = (origin is types.UnionType or origin is typing.Union) and type(
                None
            ) in args
            if not is_optional:
                required.append(f.name)

        properties[f.name] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def generate_workflow_schema() -> dict[str, Any]:
    """Generate the complete workflow YAML schema.

    Returns a dict describing the full workflow format, derived from
    the actual dataclass definitions used by the parser.
    """
    # Start with WorkflowDefinition
    base = _dataclass_to_schema(WorkflowDefinition)

    # Remove internal fields
    for field_name in _INTERNAL_FIELDS:
        base["properties"].pop(field_name, None)
        if field_name in base.get("required", []):
            base["required"].remove(field_name)

    # Enrich steps with item schema
    step_schema = _dataclass_to_schema(StepDefinition)
    base["properties"]["steps"] = {
        "type": "array",
        "items": step_schema,
        "description": "Ordered list of workflow steps. Each step uses either 'tool' or 'code' (not both).",
    }

    # Enrich inputs with accepted forms documentation
    input_full_schema = _dataclass_to_schema(InputDefinition)
    base["properties"]["inputs"] = {
        "type": "array",
        "description": "Workflow input definitions. Supports multiple syntax forms.",
        "full_form_properties": input_full_schema["properties"],
        "accepted_forms": [
            {
                "name": "string_shorthand",
                "description": "Simple required input with no default",
                "example": "- url",
            },
            {
                "name": "default_shorthand",
                "description": "Input with a default value (becomes optional)",
                "example": '- topic: "events"',
            },
            {
                "name": "full_definition",
                "description": "Full input definition with type, validation, description",
                "example": '- url:\n    type: string\n    required: true\n    description: "URL to fetch"',
            },
        ],
    }

    # Enrich outputs with accepted formats documentation
    output_schema = _dataclass_to_schema(OutputDefinition)
    base["properties"]["outputs"] = {
        "description": "Workflow output definitions. Supports list or dict format.",
        "item_properties": output_schema["properties"],
        "accepted_formats": [
            {
                "name": "dict_format",
                "description": "Dict mapping output name to definition",
                "example": "outputs:\n  result:\n    from: steps.fetch.output",
            },
            {
                "name": "list_format",
                "description": "List of output definitions with explicit name field",
                "example": "outputs:\n  - name: result\n    from_path: steps.fetch.output",
            },
        ],
    }

    # Enrich defaults
    defaults_schema = _dataclass_to_schema(WorkflowDefaults)
    base["properties"]["defaults"] = {
        **defaults_schema,
        "description": "Default settings applied to all steps unless overridden.",
    }

    # Enrich packages
    packages_schema = _dataclass_to_schema(PackagesConfig)
    base["properties"]["packages"] = {
        **packages_schema,
        "description": "Python packages configuration for code steps.",
    }

    # Add template syntax documentation
    base["template_syntax"] = {
        "description": "Template expressions use {{ }} syntax in step params",
        "examples": [
            "{{ inputs.url }}",
            "{{ steps.fetch.output }}",
            "{{ steps.transform.output.items }}",
        ],
    }

    # Add a concrete example that parses through the real parser
    base["example"] = _get_example_workflow()

    return base


def _get_example_workflow() -> str:
    """Return a concrete example workflow YAML string."""
    return """name: example-workflow
version: "1.0.0"
description: Example workflow demonstrating all features

inputs:
  - url
  - topic:
      type: string
      default: "events"
      description: "Target topic"

defaults:
  timeout: 60
  on_error: fail
  retry:
    max_attempts: 3
    backoff: exponential
    delay_seconds: 2.0

steps:
  - id: fetch
    tool: http_request
    params:
      url: "{{ inputs.url }}"
      method: GET
    timeout: 30

  - id: transform
    code: |
      import json
      data = json.loads(context.steps["fetch"].output)
      result = {"url": context.inputs["url"], "count": len(data)}
    depends_on:
      - fetch

outputs:
  result:
    from: steps.transform.output
"""
