"""Workflow schema generator.

Generates a JSON Schema description of the workflow YAML format
by introspecting the actual dataclass definitions. This ensures
the schema is always in sync with the parser (single source of truth).

S-290 P2: schema is split into a small Tier 1 minimal schema (embedded in
the workflow_create tool description) and on-demand sections fetched via
workflow_schema(section=...). The full dump is still available through
``generate_workflow_schema()`` for backward compatibility.
"""

import copy
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

# Canonical section names exposed via workflow_schema(section=...).
# Order matters — used as the deterministic "available_sections" listing.
# "discovery" leads because it is the recommended first call after the
# Tier 1 schema (see ``before_you_start`` in ``generate_tier1_schema``).
AVAILABLE_SECTIONS: tuple[str, ...] = (
    "discovery",
    "sandbox_constraints",
    "context_api",
    "tool_steps",
    "inputs",
    "outputs",
    "defaults",
    "packages",
    "examples",
)


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

    # T-E: Frame-switch annotation for top-level description field
    if "description" in base["properties"]:
        base["properties"]["description"]["description"] = (
            "Write this as the tool author, not the workflow author. "
            "This field becomes the MCP tool description that agents see in tools/list — "
            "state what the workflow does, what it returns, and when to call it over alternatives. "
            "Treat it as public API documentation."
        )

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
    # T-F: Frame-switch annotation for input description fields
    if "description" in input_full_schema.get("properties", {}):
        input_full_schema["properties"]["description"]["description"] = (
            "Write this as a parameter doc for a tool call, not an internal variable label. "
            "Agents reading tools/list use this to know what value to pass."
        )
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
            "Code steps execute Python in a sandboxed exec() environment. "
            "Set step output by assigning to the 'result' variable, or by "
            "using a top-level 'return X' (rewritten to 'result = X' + early "
            "exit). 'return X' is useful for guard clauses; 'result = X' "
            "continues execution to the next statement."
        ),
        "output": (
            "Assign to the 'result' variable to set step output, or use "
            "'return X' to set it and exit the step immediately. "
            "If neither is used, step output is None."
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
                "Returns the tool's normalized output — same shape a tool step would "
                "produce (transport envelope removed). On a transport-level error "
                "raises ToolError (pre-injected; no import needed). "
                "Max 10 calls per step. "
                "python_exec cannot call itself (recursion prevention)."
            ),
            "context.tools.call_mcp('mcp', 'tool', {...})": (
                "Call a tool by MCP server name and bare tool name. "
                "Runner is resolved implicitly from the bridge context — "
                "same resolution rules as tool steps. "
                "Returns normalized output; raises ToolError on transport errors. "
                "Preferred over call() for portable workflows."
            ),
            "ToolError": (
                "Exception raised by call()/call_mcp() when a tool returns a "
                'transport-level error envelope ({"content": ..., "error": <msg>}). '
                "Pre-injected into the sandbox scope — use without import: "
                "`try: data = await context.tools.call_mcp(...) except ToolError as e: ...`. "
                "Attributes: e.message (str), e.tool (str|None), e.mcp (str|None)."
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
            "import os     # SecurityError — os is not in allowed_imports",
            "type(x)       # NameError — type is in forbidden_builtins",
            "dir(x)        # NameError — dir is in forbidden_builtins",
            "getattr(x, k) # NameError — getattr is in forbidden_builtins; use x.key notation instead",
            "eval(s)       # NameError — eval is in forbidden_builtins",
        ],
        # S-272 T-862: authoring guidance surfaced on workflow_schema so agents
        # pick the right APIs the first time.
        "import_notes": {
            "general": ("Both 'import X' and 'from X import Y' work for all allowed modules."),
            "datetime": {
                "strptime": (
                    "datetime.strptime() works (uses _strptime internally, allowed in sandbox)"
                ),
                "fromisoformat": (
                    "datetime.fromisoformat(ts.replace('Z', '+00:00')) — recommended for ISO 8601"
                ),
            },
        },
        "common_patterns": {
            "parse_iso_timestamp": (
                "import datetime\ndt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))"
            ),
            "duration_between_timestamps": (
                "import datetime\n"
                "start = datetime.datetime.fromisoformat(started.replace('Z', '+00:00'))\n"
                "end = datetime.datetime.fromisoformat(ended.replace('Z', '+00:00'))\n"
                "duration_s = int((end - start).total_seconds())"
            ),
            "safe_json_extract": (
                "data = context.steps['step_id'].output\n"
                "# output is already normalized — access keys directly\n"
                "items = data.get('items', []) if isinstance(data, dict) else []"
            ),
        },
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
            "Call workflow_list_tools to see tools grouped by MCP server, "
            "then workflow_tool_schema for each tool's input schema."
        ),
    }

    base["authoring_tools"] = {
        "description": (
            "MCP tools for iterative workflow authoring. "
            "Recommended flow: workflow_schema → "
            'workflow_schema(section="discovery") → workflow_list_tools → '
            "workflow_tool_schema → workflow_create. "
            "Submit the YAML directly via workflow_create — do not call any "
            "separate validate tool. If workflow_create returns "
            '`status="draft"`, repair the YAML via workflow_patch using the '
            "returned `draft_id` and the `validation.errors[].suggested_fix` "
            "ops. If workflow_run fails on a registered workflow, repair it "
            "in place via workflow_patch using the engine's "
            "`error_metadata.suggested_fix`. Re-call workflow_create only "
            "to author a new workflow, never to retry a failed one."
        ),
        "tools": [
            {
                "name": "workflow_list_tools",
                "description": (
                    "List tools available for workflow steps, grouped by MCP server. "
                    "Call after workflow_schema to discover tools, or re-call after "
                    "Ploston config changes to see updated listings."
                ),
            },
            {
                "name": "workflow_tool_schema",
                "description": (
                    "Get the parameter schema for one or more tools (batch via 'tools'). "
                    "Call after workflow_list_tools to inspect schemas."
                ),
            },
            {
                "name": "workflow_call_tool",
                "description": (
                    "Call a connected MCP tool directly and see its normalized response. "
                    "Use to test tools and inspect output shapes before adding a step."
                ),
            },
            {
                "name": "workflow_patch",
                "description": (
                    "Iterate on a workflow with targeted edits — `replace` "
                    "(str_replace inside a code step), `replace_lines` "
                    "(line-range edit inside a code step, for large blocks), "
                    "`set` (scalar at a dot-path), `add_step`, `remove_step`. "
                    "Use after workflow_create returns a draft, or after "
                    "workflow_run fails on a registered workflow; pass back "
                    "the `suggested_fix` op from the prior response. "
                    "Validates and re-registers in one call; bumps the "
                    "workflow version on each live patch."
                ),
            },
            {
                "name": "workflow_list",
                "description": "List all registered workflows. Confirm registration after workflow_create.",
            },
            {
                "name": "workflow_schema",
                "description": (
                    "Return this schema document. Call workflow_list_tools to see "
                    "tools available for workflow steps."
                ),
            },
        ],
    }

    return base


def _get_example_workflow() -> str:
    """Return a concrete example workflow YAML string.

    The description and input descriptions model the tool-author frame:
    they read as MCP tool documentation, not internal workflow notes.
    """
    return """name: example-workflow
version: "1.0.0"
description: >
  Fetch items from a URL and return a count filtered by topic.
  Returns {url, count}. Use when you need a quick tally of items
  from a remote endpoint without reading individual records.

inputs:
  - url:
      type: string
      required: true
      description: "The HTTP endpoint to fetch data from (e.g. 'https://api.example.com/items')"
  - topic:
      type: string
      default: "events"
      description: "Topic keyword to filter and count items by (e.g. 'events', 'orders')"

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


# ─────────────────────────────────────────────────────────────────
# Tier 1 minimal schema + on-demand section accessors
# ─────────────────────────────────────────────────────────────────


# Principle-only guidance returned by workflow_schema(section="discovery").
# Deliberately MCP-agnostic: no concrete tool names from any specific server.
# Concrete examples for a target server come from workflow_list_tools +
# workflow_tool_schema + workflow_call_tool, on demand.
_DISCOVERY_SECTION: dict[str, Any] = {
    "purpose": (
        "Investigation discipline for the conversation that precedes "
        "workflow_create. Loaded explicitly via "
        'workflow_schema(section="discovery"). Holds principles only — '
        "concrete tool names live behind workflow_list_tools / "
        "workflow_tool_schema / workflow_call_tool."
    ),
    "principles": {
        "narrow_over_broad": (
            "Always call discovery tools with the narrowest filters they "
            "support (per_page / limit, status, branch, path, time window, "
            "tag, name prefix). Every byte returned is context the agent "
            "must re-process on the next turn. An unfiltered list call is "
            "almost always the wrong call."
        ),
        "schema_then_call": (
            "Before authoring a step against an unfamiliar tool, call "
            "workflow_tool_schema for the contract, then workflow_call_tool "
            "with realistic params to see the actual response shape. "
            "Do not infer response shapes from tool names or descriptions."
        ),
        "source_over_surface": (
            "When both a configuration source (a file, manifest, or "
            "declarative definition) and a runtime-state listing (jobs, "
            "runs, instances, events) can answer the same question, prefer "
            "the source. The source is canonical; the listing reflects "
            "history and may be partial, paginated, or rate-limited."
        ),
        "investigate_in_conversation": (
            "Do investigation up-front in the conversation, not inline as "
            "workflow steps. A workflow step should encode a validated "
            "plan against a known shape; it should not re-discover the "
            "shape at runtime. If a step needs to branch on data, encode "
            "the branch on the data — not on a fresh discovery call."
        ),
        "minimal_step_count": (
            "Prefer the smallest number of steps that produces the "
            "required output. A single tool call followed by a single "
            "code step that shapes the result is usually sufficient. "
            "Resist adding steps that re-fetch data already available "
            "in context.steps[<id>].output."
        ),
    },
    "investigation_toolbox": {
        "workflow_list_tools": (
            "Discover which MCP servers are connected and what tools they "
            "expose. Scope to specific servers via mcp_servers=[...] when "
            "you know the domain."
        ),
        "workflow_tool_schema": (
            "Pull the parameter schema for one or many tools (batch via "
            "tools=[{mcp,tool}, ...]). Read this before authoring params."
        ),
        "workflow_call_tool": (
            "Smoke-test a tool with realistic params and inspect the "
            "normalized response. Use this to confirm response shapes "
            "before encoding step params or output handling."
        ),
        "workflow_get / workflow_list": (
            "Inspect already-registered workflows. Useful for cloning, "
            "patching, or verifying a name is free before workflow_create."
        ),
    },
    "anti_patterns": [
        "Listing everything and filtering in a code step instead of passing filters to the tool.",
        "Authoring steps against an inferred response shape, then "
        "iterating via workflow_run errors instead of confirming the "
        "shape via workflow_call_tool first.",
        "Adding a discovery step inside the workflow to learn data the "
        "agent could have learned during authoring.",
        "Calling the same listing tool twice with the same params across "
        "two steps instead of reusing context.steps[<id>].output.",
    ],
    "next": (
        "After discovery: workflow_list_tools → workflow_tool_schema → "
        "workflow_create. On validation failure use workflow_patch with "
        "the returned draft_id; on runtime failure use workflow_patch "
        "with the engine's error_metadata.suggested_fix."
    ),
}


def generate_tier1_schema() -> dict[str, Any]:
    """Return the minimal schema (Tier 1) suitable for embedding in tool
    descriptions and as the default ``workflow_schema()`` no-arg response.

    Includes only what an agent needs to author a basic workflow:
        - YAML field names and types (top-level: name, version, description,
          inputs, steps, outputs, defaults, packages)
        - Step types (tool+mcp / code) and the ``result`` assignment rule
        - Template syntax
        - A pointer to ``workflow_schema(section=...)`` for deeper detail

    Target size: under 2K tokens (~6KB UTF-8). Enforced by
    ``test_tier1_under_2k_tokens``.
    """
    return {
        "before_you_start": (
            'Call workflow_schema(section="discovery") next for '
            "investigation-discipline principles (narrow filters over "
            "broad calls, schema-then-call, source-over-surface). "
            "Read these before authoring against an unfamiliar tool."
        ),
        "fields": {
            "name": "string — workflow identifier (dashes auto-replaced with underscores)",
            "version": 'string — semver, e.g. "1.0.0"',
            "description": "string — public tool description shown in tools/list",
            "inputs": "list — workflow inputs (string shorthand or full dict form)",
            "steps": "list — ordered workflow steps (tool steps or code steps)",
            "outputs": "dict or list — workflow outputs",
            "defaults": "dict — optional defaults (timeout, on_error, retry, runner)",
            "packages": "dict — optional package profile + extras for code steps",
        },
        "step_types": {
            "tool_step": (
                "Use 'tool' + 'mcp' fields. Example:\n"
                "  - id: fetch\n"
                "    tool: list_workflow_runs\n"
                "    mcp: github\n"
                "    params:\n"
                '      owner: "{{ inputs.owner }}"'
            ),
            "code_step": (
                "Use 'code' field. Assign to 'result' or use 'return X' "
                "(rewritten to 'result = X' + early exit). Example:\n"
                "  - id: transform\n"
                "    code: |\n"
                "      data = context.steps['fetch'].output\n"
                "      if not data:\n"
                "          return {'count': 0}\n"
                "      result = {'count': len(data)}\n"
                "    depends_on: [fetch]"
            ),
        },
        "template_syntax": (
            "Use {{ }} in step params: "
            "{{ inputs.x }}, {{ steps.id.output }}, {{ steps.id.output.key }}"
        ),
        "input_shorthand": (
            "- url               # required string\n"
            "- topic: events     # optional, default 'events'\n"
            "- url: {type: string, required: true, description: '...'}"
        ),
        "output_syntax": ("outputs:\n  result:\n    from: steps.transform.output"),
        "more_detail": (
            f"Call workflow_schema(section=NAME) for: {', '.join(AVAILABLE_SECTIONS)}."
        ),
    }


def _build_section(name: str, full_schema: dict[str, Any]) -> dict[str, Any]:
    """Extract a single section from the full schema dump.

    Internal helper for ``generate_section()``. Maps a canonical section
    name (see ``AVAILABLE_SECTIONS``) to the corresponding sub-tree of the
    full schema produced by ``generate_workflow_schema()``.
    """
    if name == "discovery":
        # Static, principle-only guidance — independent of the rest of the
        # generated schema. Returned as a deep copy so callers can mutate
        # the dict without affecting the module-level constant.
        return copy.deepcopy(_DISCOVERY_SECTION)
    if name == "sandbox_constraints":
        return dict(full_schema["code_steps"]["sandbox_constraints"])
    if name == "context_api":
        # Pack context_api together with the related code-step authoring
        # guidance: import notes, common patterns, anti-patterns, and the
        # short example. ToolError is documented as a context_api entry.
        cs = full_schema["code_steps"]
        return {
            "context_api": cs["context_api"],
            "import_notes": cs.get("import_notes", {}),
            "common_patterns": cs.get("common_patterns", {}),
            "anti_patterns": cs.get("anti_patterns", []),
            "example": cs.get("example", ""),
            "output": cs.get("output", ""),
        }
    if name == "tool_steps":
        return dict(full_schema["tool_steps"])
    if name == "inputs":
        return dict(full_schema["properties"]["inputs"])
    if name == "outputs":
        return dict(full_schema["properties"]["outputs"])
    if name == "defaults":
        return dict(full_schema["properties"]["defaults"])
    if name == "packages":
        return dict(full_schema["properties"]["packages"])
    if name == "examples":
        return {
            "workflow_example": full_schema.get("example", ""),
            "code_step_example": full_schema["code_steps"].get("example", ""),
            "common_patterns": full_schema["code_steps"].get("common_patterns", {}),
            "anti_patterns": full_schema["code_steps"].get("anti_patterns", []),
            "template_syntax": full_schema.get("template_syntax", {}),
        }
    raise KeyError(name)


def generate_section(name: str) -> dict[str, Any]:
    """Return a single named section of the workflow schema.

    Args:
        name: Canonical section name (must be in ``AVAILABLE_SECTIONS``).

    Returns:
        The schema fragment for the requested section.

    Raises:
        KeyError: If ``name`` is not a recognized section.
    """
    if name not in AVAILABLE_SECTIONS:
        raise KeyError(name)
    full = generate_workflow_schema()
    return _build_section(name, full)
