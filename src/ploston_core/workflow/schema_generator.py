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
    # Add descriptions for mcp field in step schema
    if "mcp" in step_schema.get("properties", {}):
        step_schema["properties"]["mcp"]["description"] = (
            "Name of the MCP server that hosts the tool. Required for tool steps. "
            "Used together with 'tool' for resolution: the CP looks up "
            "the tool by (mcp, tool) on the runner specified in defaults.runner "
            "(or the bridge's implicit runner when defaults.runner is omitted)."
        )
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
    # Add runner description
    if "runner" in defaults_schema.get("properties", {}):
        defaults_schema["properties"]["runner"]["description"] = (
            "Runner name for tool resolution. When set, all tool steps resolve "
            "against this runner's tool registry ({runner}__{mcp}__{tool}). "
            "When omitted, the bridge's implicit runner is used (from X-Bridge-Runner header). "
            "Only needed for multi-runner disambiguation or CP-direct workflows."
        )
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

    # Import sandbox constraints as the single source of truth.
    # Local import to avoid any circular dependency risk.
    from ploston_core.sandbox.sandbox import (
        DANGEROUS_BUILTINS,
        DANGEROUS_DUNDERS,
        SAFE_IMPORTS,
    )

    # Builtins that are surprising to developers — annotate them.
    _surprising_forbidden = {
        "type": "blocks runtime type inspection (isinstance() still works)",
        "dir": "blocks attribute listing",
        "getattr": "blocks dynamic attribute access",
        "setattr": "blocks dynamic attribute mutation",
        "delattr": "blocks dynamic attribute deletion",
        "hasattr": "blocks attribute existence check",
        "callable": "blocks callable check",
        "vars": "blocks variable dict access",
        "globals": "blocks global scope access",
        "locals": "blocks local scope access",
        "classmethod": "no OOP class methods",
        "staticmethod": "no OOP static methods",
        "property": "no OOP property descriptors",
        "super": "no OOP super() calls",
    }

    forbidden_builtins_annotated = []
    for name in sorted(DANGEROUS_BUILTINS):
        entry: dict[str, Any] = {"name": name}
        if name in _surprising_forbidden:
            entry["note"] = _surprising_forbidden[name]
        forbidden_builtins_annotated.append(entry)

    # Document the code step contract for agents authoring workflows
    base["code_steps"] = {
        "description": (
            "Code steps execute Python via exec(). "
            "Set step output by assigning to the 'result' variable. "
            "Do NOT use 'return' statements — they raise SyntaxError "
            "at the top level of exec()."
        ),
        "output": (
            "Assign to the 'result' variable to set step output. "
            "If 'result' is never assigned, step output is None."
        ),
        "context_api": {
            "context.inputs": (
                "dict — workflow input values. "
                "Access: context.inputs['key'] or context.inputs.get('key')"
            ),
            "context.steps['step_id'].output": (
                "The raw output value of a prior completed step. "
                "For tool steps this is the tool's return value (dict, list, str, etc). "
                "For code steps this is whatever was assigned to 'result'. "
                "Access nested keys normally: context.steps['fetch'].output.get('items', [])"
            ),
            "context.config": ("dict — workflow-level config values."),
            "context.tools.call('tool_name', {...})": (
                "Call a registered tool from within a code step by full canonical name. "
                "Returns the tool's output directly. "
                "Max 10 calls per step. "
                "python_exec cannot call itself (recursion prevention)."
            ),
            "context.tools.call_mcp('mcp', 'tool', {...})": (
                "Call a tool by MCP server name and bare tool name. "
                "Runner is resolved implicitly from the bridge context — "
                "same resolution rules as tool steps. "
                "Preferred over call() for portable workflows."
            ),
            "context.tools.call_mcp_loop_example": (
                "# Fan-out over a list (foreach not yet available — use a code step loop)\n"
                "repos = context.steps['discover'].output.get('repos', [])\n"
                "results = []\n"
                "for repo in repos:\n"
                "    data = await context.tools.call_mcp(\n"
                "        'github', 'list_commits',\n"
                "        {'owner': repo['owner'], 'repo': repo['name']}\n"
                "    )\n"
                "    results.append({'repo': repo['name'], 'commits': data})\n"
                "result = results"
            ),
            "context.log('message')": (
                "Append a debug message to the step's debug log. "
                "Messages are captured and attached to the StepOutput. "
                "Available in subsequent templates as {{ steps.<id>.debug_log }}."
            ),
            "context.workflow": (
                "WorkflowMeta object with name, version, execution_id, start_time. "
                "Access: context.workflow.name, context.workflow.version, "
                "context.workflow.start_time (ISO 8601)."
            ),
        },
        "sandbox_constraints": {
            "description": (
                "The sandbox enforces three layers of constraints. "
                "Violating any of them raises a SecurityError before execution."
            ),
            "allowed_imports": {
                "description": (
                    "Only these modules may be imported. Any other import raises SecurityError."
                ),
                "modules": sorted(SAFE_IMPORTS),
            },
            "forbidden_builtins": {
                "description": (
                    "These built-in names are removed from the execution scope. "
                    "Using them raises NameError or SecurityError. "
                    "Note: isinstance(), len(), range(), print(), str(), int(), "
                    "float(), list(), dict(), tuple(), set(), bool(), abs(), "
                    "min(), max(), sum(), round(), sorted(), enumerate(), zip(), "
                    "map(), filter() and most other builtins are available."
                ),
                "names": forbidden_builtins_annotated,
            },
            "forbidden_attribute_access": {
                "description": (
                    "Accessing these dunder attributes raises SecurityError. "
                    "This prevents sandbox escape via class hierarchy traversal "
                    "or code object manipulation."
                ),
                "attributes": sorted(DANGEROUS_DUNDERS),
            },
        },
        "example": (
            "# Read prior step output and set result\n"
            "runs = context.steps['fetch_runs'].output.get('workflow_runs', [])\n"
            "failed = next((r for r in runs if r['conclusion'] == 'failure'), None)\n"
            "if failed:\n"
            "    result = {'run_id': failed['id'], 'head_sha': failed['head_sha']}\n"
            "else:\n"
            "    result = {'run_id': None}"
        ),
        "anti_patterns": [
            "return {...}  # SyntaxError — use result = {...} instead",
            "return None   # SyntaxError — just don't assign result, or assign result = None",
            "import os     # SecurityError — os is not in allowed_imports",
            "type(x)       # NameError — type is in forbidden_builtins",
            "dir(x)        # NameError — dir is in forbidden_builtins",
            "getattr(x, k) # NameError — getattr is in forbidden_builtins; use x.key notation instead",
        ],
    }

    # Document tool step resolution rules for agents authoring workflows
    base["tool_steps"] = {
        "description": (
            "Tool steps invoke a registered tool via the 'tool' and 'mcp' fields. "
            "The 'mcp' field is required for tool steps and identifies which MCP "
            "server hosts the tool. Resolution: if defaults.runner is set (or the "
            "bridge provides X-Bridge-Runner), the CP constructs "
            "{runner}__{mcp}__{tool} and verifies it exists. If no runner is "
            "available, the CP resolves against its own directly-connected MCP "
            "servers by matching (mcp, tool)."
        ),
        "required_fields": ["id", "tool", "mcp"],
        "resolution_chain": [
            "1. runner from step → defaults.runner → bridge X-Bridge-Runner",
            "2. If runner found: lookup {runner}__{mcp}__{tool} in runner registry",
            "3. If no runner: lookup tool by name on CP-direct MCP server matching mcp",
            "4. If not found anywhere: validation error with available tools hint",
        ],
        "example": (
            "- id: fetch_runs\n"
            "  tool: list_workflow_runs\n"
            "  mcp: github\n"
            "  params:\n"
            '    owner: "{{ inputs.owner }}"\n'
            '    repo: "{{ inputs.repo }}"'
        ),
        "note": (
            "Call workflow_schema to see the live 'available_tools' list — "
            "it shows every tool grouped by MCP server and runner."
        ),
    }

    base["authoring_tools"] = {
        "description": (
            "MCP tools for iterative workflow authoring. "
            "Recommended flow: workflow_schema → author YAML → workflow_validate → workflow_create → workflow_list"
        ),
        "tools": [
            {
                "name": "workflow_validate",
                "description": (
                    "Validate a YAML workflow definition without registering it. "
                    "Returns schema errors and tool resolution warnings. "
                    "Call after every edit before workflow_create."
                ),
            },
            {
                "name": "workflow_list",
                "description": "List all registered workflows. Confirm registration after workflow_create.",
            },
            {
                "name": "workflow_schema",
                "description": (
                    "Return this schema document. Re-call after Ploston config changes "
                    "to get an updated available_tools list."
                ),
            },
        ],
    }

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
    mcp: http
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
