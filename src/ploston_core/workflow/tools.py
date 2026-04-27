"""Workflow management tools for MCP exposure.

Provides flat-named workflow management tools (workflow_schema, workflow_list,
workflow_get, workflow_create, workflow_update, workflow_delete, workflow_validate,
workflow_tool_schema, workflow_run) that delegate to WorkflowRegistry / WorkflowEngine.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ploston_core.engine.normalize import normalize_mcp_response
from ploston_core.errors import AELError, create_error

from .schema_generator import generate_workflow_schema

# Matches the top-level `name: <value>` field in a workflow YAML.
# Anchored to start-of-line (MULTILINE) to avoid rewriting `name:` fields
# that appear inside inputs/outputs/steps.
_WORKFLOW_NAME_FIELD_RE = re.compile(
    r"^(?P<prefix>name:\s*)(?P<quote>['\"]?)(?P<value>[^\s'\"#]+)(?P=quote)",
    re.MULTILINE,
)


def _sanitize_workflow_name(raw_name: str) -> str:
    """Replace dashes with underscores in a workflow name.

    Dashes are rejected in workflow names because the bare name becomes an
    MCP tool identifier and is commonly referenced from code steps and
    templates where underscore-separated identifiers are expected.
    """
    return raw_name.replace("-", "_")


def _rewrite_workflow_name_in_yaml(yaml_content: str, new_name: str) -> str:
    """Rewrite only the top-level ``name:`` value in a workflow YAML.

    Preserves comments, quoting style, and the rest of the document. Only
    the first top-level ``name:`` line is rewritten — nested ``name:`` fields
    (inputs, outputs, steps) are left untouched by the anchored regex.
    """

    def _sub(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{match.group('quote')}{new_name}{match.group('quote')}"

    return _WORKFLOW_NAME_FIELD_RE.sub(_sub, yaml_content, count=1)


def _is_context_tools_call(node: ast.Call) -> bool:
    """Return True when ``node`` matches ``context.tools.call`` or ``context.tools.call_mcp``.

    Used by the missing-await static check (S-286 / T-904) to recognise the
    sandbox tool-call surface inside code steps.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in ("call", "call_mcp"):
        return False
    val = func.value
    if not isinstance(val, ast.Attribute):
        return False
    if val.attr != "tools":
        return False
    return isinstance(val.value, ast.Name) and val.value.id == "context"


def _check_missing_await(steps: list[Any]) -> list[dict[str, Any]]:
    """Scan code steps for ``context.tools.call*`` invocations not wrapped in ``await``.

    Returns a list of warning dicts. Each entry carries ``path``, ``line``,
    and ``message``. Calls that fail to parse are skipped silently — the
    sandbox raises a clearer error at runtime. ``asyncio`` is not in
    ``SAFE_IMPORTS`` so the ``asyncio.gather`` pattern is impossible inside
    the sandbox; every non-awaited ``call``/``call_mcp`` is therefore a bug,
    but we surface this as a warning to avoid blocking registration on any
    future AST edge case.
    """
    warnings: list[dict[str, Any]] = []
    for step in steps:
        code = getattr(step, "code", None)
        if not code:
            continue
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            continue

        awaited_call_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                awaited_call_ids.add(id(node.value))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_context_tools_call(node):
                continue
            if id(node) in awaited_call_ids:
                continue
            warnings.append(
                {
                    "path": f"steps[{step.id}].code",
                    "line": node.lineno,
                    "message": (
                        "context.tools.call_mcp() or call() appears without 'await'. "
                        "Without await, the call returns a coroutine object, not the "
                        "tool result. Add 'await' before the call."
                    ),
                }
            )
    return warnings


if TYPE_CHECKING:
    from ploston_core.engine.engine import WorkflowEngine

    from .types import WorkflowDefinition

# ─────────────────────────────────────────────────────────────────
# Tool Names (static set for routing disambiguation)
# ─────────────────────────────────────────────────────────────────

WORKFLOW_MGMT_TOOL_NAMES = frozenset(
    {
        "workflow_schema",
        "workflow_list",
        "workflow_list_tools",
        "workflow_get",
        "workflow_get_definition",
        "workflow_create",
        "workflow_update",
        "workflow_delete",
        "workflow_validate",
        "workflow_patch",
        "workflow_tool_schema",
        "workflow_call_tool",
        "workflow_run",
    }
)

# Backward-compat alias (deprecated)
WORKFLOW_CRUD_TOOL_NAMES = WORKFLOW_MGMT_TOOL_NAMES

# ─────────────────────────────────────────────────────────────────
# Output schemas (static, declared per management tool)
# ─────────────────────────────────────────────────────────────────
#
# These describe the *inner JSON payload* returned by each handler --
# the dict that ``WorkflowToolsProvider.call()`` later wraps in the MCP
# ``{content:[{type:"text",text:...}], isError:false}`` envelope. The
# scope matches what ``format_inferred_schema`` produces for learned
# schemas, and what ``_TOOL_SCHEMA_RESPONSE_HINT`` promises ("transport
# envelopes are stripped").
#
# Polymorphism rule: ``oneOf`` only when presence-of-keys logically
# excludes another set (``workflow_tool_schema``). Everywhere else,
# additive metadata is captured via optional fields (``required`` lists
# are authoritative).

_OUTPUT_SCHEMA_WORKFLOW_SCHEMA = {
    "type": "object",
    "description": (
        "Either the full schema (``schema`` + ``sections`` + ``authoring_note``), "
        "a single section view (``section`` + ``schema`` [+ ``template_syntax``]), "
        "or an unknown-section error (``error`` + ``available_sections``)."
    ),
    "properties": {
        "schema": {"type": "object", "additionalProperties": True},
        "sections": {"type": "array", "items": {"type": "string"}},
        "authoring_note": {"type": "string"},
        "section": {"type": "string"},
        "template_syntax": {"type": "object", "additionalProperties": True},
        "error": {"type": "string"},
        "available_sections": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_LIST = {
    "type": "object",
    "properties": {
        "workflows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "inputs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "version", "description", "tags", "inputs"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["workflows"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_LIST_TOOLS = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mcp_server": {"type": "string"},
                    "runner": {"type": ["string", "null"]},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["mcp_server", "tools"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tools"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_GET = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "description": {"type": ["string", "null"]},
        "yaml": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "steps_count": {"type": "integer", "minimum": 0},
    },
    "required": ["name", "version", "yaml", "tags", "inputs", "steps_count"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_GET_DEFINITION = {
    "type": "object",
    "description": (
        "Includes ``yaml_content`` (re-creatable via workflow_create) plus a "
        "structured breakdown. Optional sections (``packages``, ``defaults``) "
        "appear only when present in the workflow."
    ),
    "properties": {
        "yaml_content": {"type": "string"},
        "name": {"type": "string"},
        "version": {"type": "string"},
        "description": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "packages": {"type": "object", "additionalProperties": True},
        "defaults": {"type": "object", "additionalProperties": True},
        "inputs": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "steps": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "outputs": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    },
    "required": ["yaml_content", "name", "version", "tags", "inputs", "steps", "outputs"],
    "additionalProperties": False,
}


# Shared by workflow_create / workflow_update -- same shape, ``status`` differs.
def _workflow_mutation_schema(status_enum: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "version": {"type": "string"},
            "status": {"type": "string", "enum": status_enum},
            "tool_preview": {"type": "object", "additionalProperties": True},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "name_sanitized": {
                "type": "object",
                "description": (
                    "Present only when the workflow name was rewritten "
                    "(dashes replaced with underscores)."
                ),
                "properties": {
                    "original": {"type": "string"},
                    "registered_as": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["original", "registered_as", "reason"],
                "additionalProperties": False,
            },
        },
        "required": ["name", "version", "status", "tool_preview", "warnings"],
        "additionalProperties": False,
    }


_OUTPUT_SCHEMA_WORKFLOW_CREATE = _workflow_mutation_schema(["created"])
_OUTPUT_SCHEMA_WORKFLOW_UPDATE = _workflow_mutation_schema(["updated"])

_OUTPUT_SCHEMA_WORKFLOW_DELETE = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "status": {"type": "string", "enum": ["deleted"]},
    },
    "required": ["name", "status"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_VALIDATE = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["path", "message"],
                "additionalProperties": False,
            },
        },
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                    # Optional 1-based source line for static-analysis warnings
                    # that pinpoint a specific location inside a code step
                    # (S-286 / T-904 missing-await check).
                    "line": {"type": "integer", "minimum": 1},
                },
                "required": ["path", "message"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["valid", "errors", "warnings"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_PATCH = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "status": {"type": "string", "enum": ["patched"]},
        "patches_applied": {"type": "integer", "minimum": 0},
        "tool_preview": {"type": "object", "additionalProperties": True},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "name",
        "version",
        "status",
        "patches_applied",
        "tool_preview",
        "warnings",
    ],
    "additionalProperties": False,
}

# Resolved-tool entry as built by ``_compose_schema_response``. Used both as
# the single-mode top-level shape and as the items shape of the batch-mode
# ``results`` array.
_OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA_RESOLVED = {
    "type": "object",
    "properties": {
        "mcp_server": {"type": "string"},
        "tool": {"type": "string"},
        "runner": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {
            "anyOf": [
                {"type": "object", "additionalProperties": True},
                {"type": "null"},
            ]
        },
        "source": {"type": "string", "enum": ["cp", "runner"]},
        "response_hint": {"type": "string"},
        "suggested_output_schema": {"type": "object", "additionalProperties": True},
    },
    "required": [
        "mcp_server",
        "tool",
        "runner",
        "description",
        "input_schema",
        "output_schema",
        "source",
        "response_hint",
    ],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA_NOT_FOUND = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "const": False},
        "mcp_server": {"type": "string"},
        "tool": {"type": "string"},
        "error": {"type": "string"},
        "hint": {"type": "string"},
        "response_hint": {"type": "string"},
    },
    "required": ["found", "mcp_server", "tool", "error", "hint", "response_hint"],
    "additionalProperties": False,
}

# Polymorphic — single-mode returns a resolved/not-found dict directly,
# batch-mode wraps a list under ``results``. The MCP spec requires
# ``outputSchema`` roots to be ``type: "object"``; Claude (and other strict
# clients) reject the entire ``tools/list`` response when this constraint
# is violated. Encode the union as a single object with all branch fields
# optional plus the batch ``results`` array, and document the branches in
# the ``description``. ``additionalProperties: true`` keeps validation
# permissive across branches.
_OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA = {
    "type": "object",
    "description": (
        "Polymorphic. Single-mode returns a resolved entry "
        "(``mcp_server`` + ``tool`` + ``input_schema`` + ``output_schema`` + "
        "``source`` + ``response_hint`` [+ ``suggested_output_schema``]) or a "
        "not-found entry (``found: false`` + ``mcp_server`` + ``tool`` + "
        "``error`` + ``hint`` + ``response_hint``). Batch-mode wraps a list "
        "of those entries under ``results``."
    ),
    "properties": {
        # Resolved-entry fields.
        "mcp_server": {"type": "string"},
        "tool": {"type": "string"},
        "runner": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {
            "anyOf": [
                {"type": "object", "additionalProperties": True},
                {"type": "null"},
            ]
        },
        "source": {"type": "string", "enum": ["cp", "runner"]},
        "response_hint": {"type": "string"},
        "suggested_output_schema": {"type": "object", "additionalProperties": True},
        # Not-found-entry fields.
        "found": {"type": "boolean"},
        "error": {"type": "string"},
        "hint": {"type": "string"},
        # Batch-mode field.
        "results": {
            "type": "array",
            "items": {
                "oneOf": [
                    _OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA_RESOLVED,
                    _OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA_NOT_FOUND,
                ]
            },
        },
    },
    "additionalProperties": True,
}

_OUTPUT_SCHEMA_WORKFLOW_CALL_TOOL = {
    "type": "object",
    "description": (
        "Mirrors the per-step output an agent would see in "
        "``context.steps[id].output``. ``output`` is normalized via "
        "``normalize_mcp_response`` so transport envelopes are stripped; its "
        "exact shape depends on the underlying tool."
    ),
    "properties": {
        "success": {"type": "boolean"},
        "mcp_server": {"type": "string"},
        "tool": {"type": "string"},
        "runner": {"type": ["string", "null"]},
        "source": {"type": ["string", "null"]},
        "output": {},
        "duration_ms": {"type": ["number", "null"]},
        "error": {"type": "string"},
        "error_code": {"type": ["string", "null"]},
        "hint": {"type": "string"},
    },
    "required": ["success", "mcp_server", "tool"],
    "additionalProperties": True,
}

_OUTPUT_SCHEMA_WORKFLOW_RUN = {
    "type": "object",
    "description": (
        "Built by ``WorkflowExecutionResult.to_mcp_response``. ``result`` is "
        "the workflow's declared outputs and varies per workflow."
    ),
    "properties": {
        "execution_id": {"type": "string"},
        "workflow_version": {"type": ["string", "null"]},
        "status": {"type": "string"},
        "result": {"type": "object", "additionalProperties": True},
        "execution": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": ["number", "null"]},
                "steps": {"type": "object", "additionalProperties": True},
            },
            "required": ["duration_ms", "steps"],
            "additionalProperties": False,
        },
        "error": {"type": ["string", "null"]},
    },
    "required": ["execution_id", "status", "result", "execution"],
    "additionalProperties": False,
}


WORKFLOW_SCHEMA_TOOL = {
    "name": "workflow_schema",
    "description": (
        "Get the workflow YAML schema documentation. "
        "Returns the complete structure for authoring workflow YAML files, "
        "including all fields, types, defaults, accepted syntax variants, "
        "and a concrete example. "
        "Call workflow_list_tools to discover tools available for workflow steps."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Optional section to get schema for. Omit for full schema.",
                "examples": ["inputs", "steps", "outputs", "defaults", "packages"],
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_SCHEMA,
}

WORKFLOW_LIST_TOOL = {
    "name": "workflow_list",
    "description": (
        "List all registered workflows. Returns name, version, description, tags, and "
        "input names for each. Use for discovery — call workflow_get for the full YAML "
        "when you need to inspect or edit a specific workflow."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "Filter workflows by tag.",
            },
            "search": {
                "type": "string",
                "description": "Search in workflow name and description.",
            },
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_LIST,
}

WORKFLOW_GET_TOOL = {
    "name": "workflow_get",
    "description": (
        "Get a workflow's full YAML definition, version, tags, and step count by name. "
        "Use workflow_list first to discover available workflow names."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Workflow name.",
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_GET,
}

WORKFLOW_GET_DEFINITION_TOOL = {
    "name": "workflow_get_definition",
    "description": (
        "Get a workflow's complete definition. Returns 'yaml_content' that can be passed "
        "directly to workflow_create to re-create the workflow, plus a structured "
        "breakdown (inputs, steps, outputs, defaults, packages) for inspection. "
        "Use this for exporting, backing up, or cloning workflows."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Workflow name.",
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_GET_DEFINITION,
}

WORKFLOW_CREATE_TOOL = {
    "name": "workflow_create",
    "description": (
        "Publish a workflow as an MCP tool. Once registered, the workflow appears in "
        "tools/list under its bare name — its `description` field becomes what agents "
        "see when selecting tools, and each input `description` becomes a parameter doc. "
        "Use workflow_schema first to learn the YAML format. "
        "Returns a preview of how the tool will appear in tools/list. "
        "Note: dashes ('-') in the workflow name are automatically replaced with "
        "underscores ('_'). If a rename occurred, the response includes a "
        "`name_sanitized` field with the original name for reference."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["yaml_content"],
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": (
                    "Workflow definition in YAML format. Call workflow_schema to see the "
                    "expected structure. Workflow names must not contain dashes — any "
                    "dashes are silently replaced with underscores before registration."
                ),
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_CREATE,
}

WORKFLOW_UPDATE_TOOL = {
    "name": "workflow_update",
    "description": (
        "Update a published workflow tool. The workflow's `description` and input "
        "`description` fields are the public tool interface seen by other agents — "
        "update them as you would update tool documentation. "
        "Use workflow_get to retrieve the current definition. "
        "Returns a preview of how the updated tool will appear in tools/list. "
        "Note: dashes ('-') in the workflow name are automatically replaced with "
        "underscores ('_') — both the `name` parameter and the name inside "
        "`yaml_content` are sanitized the same way."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["name", "yaml_content"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Name of the workflow to update. Dashes are replaced with "
                    "underscores before lookup."
                ),
            },
            "yaml_content": {
                "type": "string",
                "description": (
                    "Updated workflow definition in YAML format. Call workflow_schema to "
                    "see the expected structure. Workflow names must not contain dashes — "
                    "any dashes are silently replaced with underscores."
                ),
            },
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_UPDATE,
}

WORKFLOW_DELETE_TOOL = {
    "name": "workflow_delete",
    "description": "Delete a registered workflow by name.",
    "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the workflow to delete.",
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_DELETE,
}

WORKFLOW_VALIDATE_TOOL = {
    "name": "workflow_validate",
    "description": (
        "Validate workflow YAML without registering it. "
        "Use workflow_schema to see the expected YAML format."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["yaml_content"],
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": "Workflow YAML to validate.",
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_VALIDATE,
}

WORKFLOW_PATCH_TOOL = {
    "name": "workflow_patch",
    "description": (
        "Apply targeted str_replace edits to a registered workflow's code "
        "step bodies without resubmitting the full YAML. Each patch finds "
        "an exact substring inside a single step's `code` block (must be "
        "unique within that step) and replaces it. The full workflow is "
        "re-registered under the new ``version`` after all patches succeed; "
        "if any patch fails, no changes are persisted. Use workflow_get to "
        "review the current YAML, then call this with a list of patches "
        "and a new version. Note: dashes ('-') in the workflow name are "
        "automatically replaced with underscores ('_') for lookup."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["name", "version", "patches"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Workflow name to patch. Dashes are replaced with underscores before lookup."
                ),
            },
            "version": {
                "type": "string",
                "description": (
                    "New version string for the patched workflow "
                    "(e.g. '2.1.0'). Required — patches always produce a "
                    "new revision so callers can track edits."
                ),
            },
            "patches": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "List of str_replace edits applied in order to individual code steps."
                ),
                "items": {
                    "type": "object",
                    "required": ["step_id", "old", "new"],
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "ID of the code step to patch.",
                        },
                        "old": {
                            "type": "string",
                            "description": (
                                "Exact substring to find in the step's "
                                "code body. Must occur exactly once."
                            ),
                        },
                        "new": {
                            "type": "string",
                            "description": "Replacement substring.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_PATCH,
}

WORKFLOW_TOOL_SCHEMA_TOOL = {
    "name": "workflow_tool_schema",
    "description": (
        "Get the full parameter schema for one or more tools. "
        "Returns tool descriptions and inputSchemas so you can correctly "
        "fill the params block when authoring a workflow. "
        "Single mode: pass 'mcp' and 'tool'. Batch mode: pass 'tools' as "
        "a list of {mcp, tool} objects — results are returned in the same "
        "order. Use workflow_list_tools first to discover available tools. "
        "After confirming a schema, use workflow_call_tool to test invocation."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "mcp": {
                "type": "string",
                "description": (
                    "MCP server name hosting the tool (e.g. github, filesystem, system). "
                    "Required in single mode. Must match a value from workflow_list_tools."
                ),
            },
            "tool": {
                "type": "string",
                "description": (
                    "Bare tool name as registered on the MCP server "
                    "(e.g. actions_list, read_file). Required in single mode. "
                    "Do not include runner or mcp prefix."
                ),
            },
            "tools": {
                "type": "array",
                "description": (
                    "Batch mode: list of {mcp, tool} objects to resolve in one call. "
                    "Mutually exclusive with top-level 'mcp'/'tool' (batch takes precedence)."
                ),
                "items": {
                    "type": "object",
                    "required": ["mcp", "tool"],
                    "properties": {
                        "mcp": {"type": "string"},
                        "tool": {"type": "string"},
                    },
                },
            },
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_TOOL_SCHEMA,
}

WORKFLOW_RUN_TOOL: dict[str, Any] = {
    "name": "workflow_run",
    "description": (
        "Execute a registered workflow by name with the given inputs. "
        "Returns workflow outputs on success, or a structured error on failure. "
        "Use after workflow_create or workflow_update to verify the workflow behaves correctly."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the workflow to execute.",
            },
            "inputs": {
                "type": "object",
                "description": "Input parameters for the workflow. Keys must match the workflow's declared inputs.",
                "additionalProperties": True,
            },
        },
        "required": ["name"],
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_RUN,
}

WORKFLOW_LIST_TOOLS_TOOL: dict[str, Any] = {
    "name": "workflow_list_tools",
    "description": (
        "List tools available for use in workflow steps, grouped by MCP server. "
        "Use mcp_servers to scope to specific servers (e.g. ['github', 'slack']). "
        "Omit to list all. Then call workflow_tool_schema to get input schemas, "
        "or workflow_call_tool to test a tool directly."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "mcp_servers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of MCP server names to filter by. "
                    "Accepts multiple servers for parallel lookup. "
                    "Omit to list tools from all servers."
                ),
            }
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_LIST_TOOLS,
}

WORKFLOW_CALL_TOOL_TOOL: dict[str, Any] = {
    "name": "workflow_call_tool",
    "description": (
        "Call any connected MCP tool directly and see its response. "
        "Use this to test tools and inspect response shapes before "
        "authoring a workflow. The response is normalized to match "
        "what context.steps[id].output would contain in a workflow step. "
        "Use workflow_list_tools to discover available tools first."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "mcp": {
                "type": "string",
                "description": "MCP server name (e.g. 'github', 'slack').",
            },
            "tool": {
                "type": "string",
                "description": "Tool name as shown by workflow_list_tools.",
            },
            "params": {
                "type": "object",
                "additionalProperties": True,
                "description": "Tool parameters. Use workflow_tool_schema to see expected params.",
            },
        },
        "required": ["mcp", "tool"],
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_CALL_TOOL,
}

ALL_WORKFLOW_MGMT_TOOLS: list[dict[str, Any]] = [
    WORKFLOW_SCHEMA_TOOL,
    WORKFLOW_LIST_TOOL,
    WORKFLOW_LIST_TOOLS_TOOL,
    WORKFLOW_GET_TOOL,
    WORKFLOW_GET_DEFINITION_TOOL,
    WORKFLOW_CREATE_TOOL,
    WORKFLOW_UPDATE_TOOL,
    WORKFLOW_DELETE_TOOL,
    WORKFLOW_VALIDATE_TOOL,
    WORKFLOW_PATCH_TOOL,
    WORKFLOW_TOOL_SCHEMA_TOOL,
    WORKFLOW_CALL_TOOL_TOOL,
    WORKFLOW_RUN_TOOL,
]

# Backward-compat alias (deprecated)
ALL_WORKFLOW_CRUD_TOOLS = ALL_WORKFLOW_MGMT_TOOLS

# Map of management tool name -> outputSchema (or None). Used by
# ``WorkflowToolsProvider.call`` to populate ``structuredContent`` whenever
# the tool definition declares an output schema. Per the MCP spec
# (2025-06-18 §tools/structured-content), strict clients raise
# ``-32600 Tool ... has an output schema but did not return structured
# content`` when the response omits ``structuredContent`` for a tool whose
# ``outputSchema`` is non-null.
_MGMT_TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any] | None] = {
    tool["name"]: tool.get("outputSchema") for tool in ALL_WORKFLOW_MGMT_TOOLS
}


# ─────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────


class WorkflowToolsProvider:
    """Provides workflow management tools for MCP exposure.

    Sits alongside WorkflowRegistry, delegates all operations to it.
    Exposed via MCPFrontend in running mode.
    """

    def __init__(
        self,
        workflow_registry: Any,
        tool_registry: Any | None = None,
        runner_registry: Any | None = None,
        workflow_engine: WorkflowEngine | None = None,
        tool_invoker: Any | None = None,
        schema_store: Any | None = None,
        on_tools_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        from .registry import WorkflowRegistry

        self._registry: WorkflowRegistry = workflow_registry
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
        self._workflow_engine = workflow_engine
        self._tool_invoker = tool_invoker
        # F-088 · T-890: optional schema store for surfacing learned
        # outputSchema in workflow_tool_schema responses.
        self._schema_store = schema_store
        self._on_tools_changed = on_tools_changed
        self._handlers: dict[str, Callable[..., Any]] = {
            "workflow_schema": self._handle_schema,
            "workflow_list": self._handle_list,
            "workflow_list_tools": self._handle_list_tools,
            "workflow_get": self._handle_get,
            "workflow_get_definition": self._handle_get_definition,
            "workflow_create": self._handle_create,
            "workflow_update": self._handle_update,
            "workflow_delete": self._handle_delete,
            "workflow_validate": self._handle_validate,
            "workflow_patch": self._handle_patch,
            "workflow_tool_schema": self._handle_tool_schema,
            "workflow_call_tool": self._handle_call_tool,
            "workflow_run": self._handle_run,
        }

    @staticmethod
    def is_mgmt_tool(name: str) -> bool:
        """Check if a tool name is a workflow management tool."""
        return name in WORKFLOW_MGMT_TOOL_NAMES

    @staticmethod
    def is_crud_tool(name: str) -> bool:
        """Deprecated alias for is_mgmt_tool."""
        return name in WORKFLOW_MGMT_TOOL_NAMES

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas for all management tools.

        Attaches _ploston_tags for pre-serialization filtering (DEC-170).
        """
        return [
            {**tool, "_ploston_tags": {"kind:workflow_mgmt"}} for tool in ALL_WORKFLOW_MGMT_TOOLS
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route tool call to handler. Returns MCP-format response.

        When the tool's definition declares an ``outputSchema``, the
        response also carries ``structuredContent``: the MCP spec requires
        it (strict clients raise ``-32600`` otherwise) and ``content``
        remains as the legacy text fallback for clients that don't read
        structured content.
        """
        handler = self._handlers.get(name)
        if not handler:
            raise create_error("TOOL_NOT_AVAILABLE", tool_name=name)
        result = await handler(arguments)
        response: dict[str, Any] = {
            "content": [{"type": "text", "text": _to_json(result)}],
            "isError": False,
        }
        if _MGMT_TOOL_OUTPUT_SCHEMAS.get(name) is not None and isinstance(result, dict):
            response["structuredContent"] = result
        return response

    async def _notify_tools_changed(self) -> None:
        """Fire the on_tools_changed callback if registered."""
        if self._on_tools_changed:
            await self._on_tools_changed()

    # ── Handlers ──────────────────────────────────────────────────

    async def _handle_schema(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_schema tool call.

        Returns the static YAML schema documentation. Use workflow_list_tools
        to discover tools available for workflow steps.
        """
        section = arguments.get("section")
        schema = generate_workflow_schema()

        if section:
            props = schema.get("properties", {})
            if section not in props:
                return {
                    "error": f"Unknown section: {section}",
                    "available_sections": list(props.keys()),
                }
            result: dict[str, Any] = {"section": section, "schema": props[section]}
            if section == "steps" and "template_syntax" in schema:
                result["template_syntax"] = schema["template_syntax"]
            return result

        return {
            "authoring_note": "Author the workflow. Document the tool.",
            "sections": list(schema.get("properties", {}).keys()),
            "schema": schema,
        }

    async def _handle_list_tools(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_list_tools tool call (S-277 / DEC-185).

        Returns tools available for workflow steps, grouped by MCP server.
        Optionally scoped to a subset of MCP server names.
        """
        mcp_servers = arguments.get("mcp_servers")
        groups = self._build_available_tools()
        if mcp_servers:
            allowed = set(mcp_servers)
            groups = [g for g in groups if g["mcp_server"] in allowed]
        return {"tools": groups}

    # ── Dynamic tool discovery ────────────────────────────────────

    def _build_available_tools(self) -> list[dict[str, Any]]:
        """Build available_tools list grouped by MCP server.

        Returns a list of dicts, each containing:
          - mcp_server: str — the MCP server name
          - runner: str | None — which runner hosts it (None for CP-direct)
          - tools: list[str] — bare tool names available on that server
        """
        groups: list[dict[str, Any]] = []

        # 1. CP-direct tools from ToolRegistry
        if self._tool_registry:
            try:
                all_tools = self._tool_registry.list_tools()
                # Group by server_name
                by_server: dict[str, list[str]] = {}
                for tool_def in all_tools:
                    server = getattr(tool_def, "server_name", None) or "unknown"
                    by_server.setdefault(server, []).append(tool_def.name)
                for server_name, tool_names in sorted(by_server.items()):
                    groups.append(
                        {
                            "mcp_server": server_name,
                            "runner": None,
                            "tools": sorted(tool_names),
                        }
                    )
            except Exception:
                pass  # Graceful degradation if registry not ready

        # 2. Runner-hosted tools from RunnerRegistry
        if self._runner_registry:
            try:
                runners = self._runner_registry.list()
                for runner in runners:
                    runner_name = runner.name if hasattr(runner, "name") else str(runner)
                    available = getattr(runner, "available_tools", None) or []
                    # available_tools are prefixed: "mcp__tool"
                    # Items can be strings or dicts with a "name" key (full schema)
                    runner_by_server: dict[str, list[str]] = {}
                    for tool_entry in available:
                        # Extract tool name from str or dict
                        if isinstance(tool_entry, str):
                            tool_name = tool_entry
                        elif isinstance(tool_entry, dict):
                            tool_name = tool_entry.get("name", "")
                        else:
                            continue
                        if not tool_name:
                            continue
                        parts = tool_name.split("__", 1)
                        if len(parts) == 2:
                            server, tool = parts
                            runner_by_server.setdefault(server, []).append(tool)
                    for server_name, tool_names in sorted(runner_by_server.items()):
                        groups.append(
                            {
                                "mcp_server": server_name,
                                "runner": runner_name,
                                "tools": sorted(tool_names),
                            }
                        )
            except Exception:
                pass  # Graceful degradation

        return groups

    async def _handle_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_list tool call."""
        workflows = self._registry.list_workflows()

        tag = arguments.get("tag")
        if tag:
            workflows = [w for w in workflows if tag in (w.tags or [])]

        search = arguments.get("search")
        if search:
            search_lower = search.lower()
            workflows = [
                w
                for w in workflows
                if search_lower in w.name.lower()
                or (w.description and search_lower in w.description.lower())
            ]

        return {
            "workflows": [
                {
                    "name": w.name,
                    "version": w.version,
                    "description": w.description,
                    "tags": w.tags or [],
                    "inputs": [inp.name for inp in w.inputs] if w.inputs else [],
                }
                for w in workflows
            ]
        }

    async def _handle_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_get tool call."""
        name = arguments.get("name")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")

        workflow = self._registry.get(name)
        if not workflow:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)

        return {
            "name": workflow.name,
            "version": workflow.version,
            "description": workflow.description,
            "yaml": workflow.yaml_content or f"name: {workflow.name}\nversion: {workflow.version}",
            "tags": workflow.tags or [],
            "inputs": [inp.name for inp in workflow.inputs] if workflow.inputs else [],
            "steps_count": len(workflow.steps),
        }

    async def _handle_get_definition(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_get_definition tool call.

        Returns ``yaml_content`` (pass directly to ``workflow_create``) plus a
        structured breakdown for inspection.
        """
        name = arguments.get("name")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")

        workflow = self._registry.get(name)
        if not workflow:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)

        # Primary payload — directly consumable by workflow_create
        definition: dict[str, Any] = {
            "yaml_content": workflow.yaml_content
            or f"name: {workflow.name}\nversion: {workflow.version}",
        }

        # Structured breakdown for agent/human inspection
        definition["name"] = workflow.name
        definition["version"] = workflow.version
        definition["description"] = workflow.description
        definition["tags"] = workflow.tags or []

        # Packages
        if workflow.packages:
            definition["packages"] = {
                "profile": workflow.packages.profile,
                "additional": workflow.packages.additional,
            }

        # Defaults
        if workflow.defaults:
            defaults: dict[str, Any] = {"timeout": workflow.defaults.timeout}
            if workflow.defaults.on_error:
                defaults["on_error"] = workflow.defaults.on_error.value
            if workflow.defaults.retry:
                defaults["retry"] = {
                    "max_attempts": workflow.defaults.retry.max_attempts,
                    "backoff": workflow.defaults.retry.backoff.value,
                    "delay_seconds": workflow.defaults.retry.delay_seconds,
                }
            if workflow.defaults.runner:
                defaults["runner"] = workflow.defaults.runner
            definition["defaults"] = defaults

        # Inputs (full detail)
        definition["inputs"] = [
            {
                "name": inp.name,
                "type": inp.type,
                "required": inp.required,
                "default": inp.default,
                "description": inp.description,
                **({"enum": inp.enum} if inp.enum else {}),
                **({"pattern": inp.pattern} if inp.pattern else {}),
                **({"minimum": inp.minimum} if inp.minimum is not None else {}),
                **({"maximum": inp.maximum} if inp.maximum is not None else {}),
            }
            for inp in workflow.inputs
        ]

        # Steps (full detail)
        definition["steps"] = [
            {
                "id": step.id,
                **({"tool": step.tool} if step.tool else {}),
                **({"code": step.code} if step.code else {}),
                **({"mcp": step.mcp} if step.mcp else {}),
                **({"params": step.params} if step.params else {}),
                **({"depends_on": step.depends_on} if step.depends_on else {}),
                **({"when": step.when} if step.when else {}),
                **({"on_error": step.on_error.value} if step.on_error else {}),
                **({"timeout": step.timeout} if step.timeout else {}),
                **({"on_missing_tool": step.on_missing_tool.value} if step.on_missing_tool else {}),
                **(
                    {
                        "retry": {
                            "max_attempts": step.retry.max_attempts,
                            "backoff": step.retry.backoff.value,
                            "delay_seconds": step.retry.delay_seconds,
                        }
                    }
                    if step.retry
                    else {}
                ),
            }
            for step in workflow.steps
        ]

        # Outputs
        definition["outputs"] = [
            {
                "name": out.name,
                **({"from_path": out.from_path} if out.from_path else {}),
                **({"value": out.value} if out.value else {}),
                **({"description": out.description} if out.description else {}),
            }
            for out in workflow.outputs
        ]

        return definition

    @staticmethod
    def _build_tool_preview(workflow: WorkflowDefinition) -> tuple[dict[str, Any], list[str]]:
        """Build tool-listing preview and description quality warnings.

        Returns:
            Tuple of (tool_preview dict, list of warning strings).
        """
        description = workflow.description or f"Execute {workflow.name} workflow"
        is_fallback = not workflow.description

        inputs_preview: list[dict[str, Any]] = []
        if workflow.inputs:
            for inp in workflow.inputs:
                inputs_preview.append(
                    {
                        "name": inp.name,
                        "description": inp.description or "(no description)",
                        "has_description": bool(inp.description),
                    }
                )

        warnings: list[str] = []
        if is_fallback:
            warnings.append(
                "description is empty — agents will see the generic fallback "
                f"'Execute {workflow.name} workflow'. Write description as tool documentation."
            )
        if inputs_preview and any(not i["has_description"] for i in inputs_preview):
            missing = [i["name"] for i in inputs_preview if not i["has_description"]]
            warnings.append(
                f"Input(s) {missing} have no description — agents will see '(no description)' "
                "as the parameter doc."
            )

        # F-088 T-902: surface the ``outputs:`` section so the authoring agent
        # sees what calling agents will receive.
        outputs_preview: dict[str, Any] = {}
        if workflow.outputs:
            for out in workflow.outputs:
                entry: dict[str, str] = {}
                if out.from_path:
                    entry["from"] = out.from_path
                if out.description:
                    entry["description"] = out.description
                outputs_preview[out.name] = entry or "(no metadata)"
        else:
            warnings.append(
                "No outputs defined — calling agents won't know what this workflow "
                "returns. Add an 'outputs:' section to document the return shape."
            )

        tool_preview = {
            "note": "Agents will see this tool in tools/list:",
            "tool_name": workflow.name,
            "tool_description": description,
            "parameters": {inp["name"]: inp["description"] for inp in inputs_preview},
            "outputs": outputs_preview if outputs_preview else "(no outputs defined)",
        }
        return tool_preview, warnings

    async def _handle_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_create tool call."""
        yaml_content = arguments.get("yaml_content")
        if not yaml_content:
            raise create_error("PARAM_INVALID", message="'yaml_content' parameter is required")

        from .parser import parse_workflow_yaml

        # Parse first to get name/version for the response
        workflow = parse_workflow_yaml(yaml_content)

        # Sanitize: workflow names must not contain dashes. Rewrite both the
        # parsed object and the YAML text so the persisted file matches what
        # the registry stores in memory.
        original_name = workflow.name
        sanitized_name = _sanitize_workflow_name(original_name)
        name_was_sanitized = sanitized_name != original_name
        if name_was_sanitized:
            yaml_content = _rewrite_workflow_name_in_yaml(yaml_content, sanitized_name)
            workflow.name = sanitized_name

        # register_from_yaml raises AELError(INPUT_INVALID) on validation failure
        self._registry.register_from_yaml(yaml_content, persist=True)
        await self._notify_tools_changed()

        tool_preview, warnings = self._build_tool_preview(workflow)
        response: dict[str, Any] = {
            "name": workflow.name,
            "version": workflow.version,
            "status": "created",
            "tool_preview": tool_preview,
            "warnings": warnings,
        }
        if name_was_sanitized:
            response["name_sanitized"] = {
                "original": original_name,
                "registered_as": sanitized_name,
                "reason": "dashes replaced with underscores",
            }
        return response

    async def _handle_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_update tool call."""
        name = arguments.get("name")
        yaml_content = arguments.get("yaml_content")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")
        if not yaml_content:
            raise create_error("PARAM_INVALID", message="'yaml_content' parameter is required")

        # Sanitize the lookup name first — workflows are always stored under
        # the dashless form, so a caller passing "my-flow" must resolve to
        # the "my_flow" entry.
        name = _sanitize_workflow_name(name)

        existing = self._registry.get(name)
        if not existing:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)

        from .parser import parse_workflow_yaml

        workflow = parse_workflow_yaml(yaml_content)

        # Sanitize the name inside the YAML body too — dashes are never
        # allowed to reach the registry, so the YAML's `name:` field is
        # rewritten before comparison and registration.
        original_yaml_name = workflow.name
        sanitized_yaml_name = _sanitize_workflow_name(original_yaml_name)
        yaml_name_was_sanitized = sanitized_yaml_name != original_yaml_name
        if yaml_name_was_sanitized:
            yaml_content = _rewrite_workflow_name_in_yaml(yaml_content, sanitized_yaml_name)
            workflow.name = sanitized_yaml_name

        if workflow.name != name:
            raise create_error(
                "INPUT_INVALID",
                message=f"Workflow name '{workflow.name}' does not match '{name}'",
            )

        self._registry.unregister(name)
        # register_from_yaml raises AELError(INPUT_INVALID) on validation failure
        self._registry.register_from_yaml(yaml_content, persist=True)
        await self._notify_tools_changed()

        tool_preview, warnings = self._build_tool_preview(workflow)
        response: dict[str, Any] = {
            "name": workflow.name,
            "version": workflow.version,
            "status": "updated",
            "tool_preview": tool_preview,
            "warnings": warnings,
        }
        if yaml_name_was_sanitized:
            response["name_sanitized"] = {
                "original": original_yaml_name,
                "registered_as": sanitized_yaml_name,
                "reason": "dashes replaced with underscores",
            }
        return response

    async def _handle_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_delete tool call."""
        name = arguments.get("name")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")

        if not self._registry.unregister(name):
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)

        await self._notify_tools_changed()
        return {"name": name, "status": "deleted"}

    async def _handle_validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_validate tool call."""
        yaml_content = arguments.get("yaml_content")
        if not yaml_content:
            raise create_error("PARAM_INVALID", message="'yaml_content' parameter is required")

        result = self._registry.validate_yaml(yaml_content)

        # Description quality check + missing-await check (warnings only —
        # never affect ``valid``). Both are best-effort: any parse or
        # analysis failure degrades to an empty list.
        desc_warnings: list[dict[str, Any]] = []
        async_warnings: list[dict[str, Any]] = []
        try:
            from .parser import parse_workflow_yaml

            wf = parse_workflow_yaml(yaml_content)
            fallback = f"Execute {wf.name} workflow"
            if not wf.description or wf.description.strip() == fallback:
                desc_warnings.append(
                    {
                        "path": "description",
                        "message": (
                            "description is empty or generic — agents will see a fallback string in "
                            "tools/list. Write it as tool documentation: purpose, return value, when to use."
                        ),
                    }
                )
            if wf.inputs:
                for inp in wf.inputs:
                    if not inp.description:
                        desc_warnings.append(
                            {
                                "path": f"inputs[{inp.name}].description",
                                "message": (
                                    f"Input '{inp.name}' has no description — agents will see no "
                                    "parameter doc for this field in tools/list."
                                ),
                            }
                        )
            if wf.steps:
                async_warnings = _check_missing_await(wf.steps)
        except Exception:
            desc_warnings = []
            async_warnings = []

        return {
            "valid": result.valid,
            "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            "warnings": (
                [{"path": w.path, "message": w.message} for w in result.warnings]
                + desc_warnings
                + async_warnings
            ),
        }

    async def _handle_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_patch tool call.

        Applies a list of str_replace edits to individual code steps in a
        registered workflow's stored YAML, bumps the version, and re-registers
        the result. Round-trip preservation is handled by ``ruamel.yaml`` so
        comments and the ``code: |`` block scalar style are kept intact.

        All patches must succeed before any change is persisted.
        """
        from io import StringIO

        from ruamel.yaml import YAML

        name = arguments.get("name")
        version = arguments.get("version")
        patches = arguments.get("patches")

        # Match existing handler pattern (_handle_update et al): use
        # PARAM_INVALID with tool_name for parity even though the
        # ``message`` kwarg is templated out by the error registry.
        if not name:
            raise create_error("PARAM_INVALID", tool_name="workflow_patch")
        if not version:
            raise create_error("PARAM_INVALID", tool_name="workflow_patch")
        if not patches:
            raise create_error("PARAM_INVALID", tool_name="workflow_patch")

        name = _sanitize_workflow_name(name)
        existing = self._registry.get(name)
        if not existing:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_id=name)

        yaml_content = existing.yaml_content
        if not yaml_content:
            raise create_error(
                "INPUT_INVALID",
                detail=(f"Workflow '{name}' has no stored YAML content available for patching"),
            )

        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        # Match ploston's authored YAML style: ``  - id:`` for sequence items
        # under a mapping. Without this, ruamel emits ``- id:`` flush left
        # which is valid but produces a noisy diff against existing files.
        yaml_rt.indent(mapping=2, sequence=4, offset=2)
        try:
            data = yaml_rt.load(yaml_content)
        except Exception as exc:
            raise create_error(
                "INTERNAL",
                message=f"Stored YAML for workflow '{name}' could not be parsed: {exc}",
            ) from exc

        steps_list = data.get("steps") or []
        for index, patch in enumerate(patches):
            step_id = patch.get("step_id")
            old = patch.get("old")
            new = patch.get("new")
            if not step_id or old is None or new is None:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"patches[{index}] must include non-empty 'step_id', "
                        "'old', and 'new' fields"
                    ),
                )

            step_data = next((s for s in steps_list if s.get("id") == step_id), None)
            if step_data is None:
                raise create_error(
                    "INPUT_INVALID",
                    detail=f"Step '{step_id}' not found in workflow '{name}'",
                )

            code = step_data.get("code")
            if code is None:
                raise create_error(
                    "INPUT_INVALID",
                    detail=f"Step '{step_id}' has no 'code' block to patch",
                )

            code_str = str(code)
            occurrences = code_str.count(old)
            if occurrences == 0:
                raise create_error(
                    "INPUT_INVALID",
                    detail=f"'old' substring not found in step '{step_id}' code",
                )
            if occurrences > 1:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"'old' substring appears {occurrences} times in step "
                        f"'{step_id}' code — must be unique"
                    ),
                )

            step_data["code"] = code_str.replace(old, new, 1)

        data["version"] = version

        stream = StringIO()
        yaml_rt.dump(data, stream)
        patched_yaml = stream.getvalue()

        self._registry.unregister(name)
        self._registry.register_from_yaml(patched_yaml, persist=True)
        await self._notify_tools_changed()

        from .parser import parse_workflow_yaml

        workflow = parse_workflow_yaml(patched_yaml)
        tool_preview, warnings = self._build_tool_preview(workflow)

        return {
            "name": workflow.name,
            "version": workflow.version,
            "status": "patched",
            "patches_applied": len(patches),
            "tool_preview": tool_preview,
            "warnings": warnings,
        }

    # S-272 T-863: shared hint surfaced by workflow_tool_schema on all branches.
    # MCP tool definitions only carry input schemas, so agents need an explicit
    # pointer to inspect the actual response shape at runtime.
    #
    # Inspector Schema Visibility (M-074): when ``suggested_output_schema`` is
    # present, it may be low-confidence -- this surface is pull-based and
    # bypasses the agent-facing injection floor on ``tools/list``. Agents
    # should consult ``x-confidence`` and ``x-observation_count`` on the
    # learned schema before relying on it for ``result_path`` choices; cross-
    # check with ``workflow_run`` + ``context.log()`` for any structural
    # decisions.
    _TOOL_SCHEMA_RESPONSE_HINT = (
        "Output schemas are not available from MCP tool definitions. "
        "Use workflow_run with context.log() to inspect actual response shapes. "
        "Tool step outputs are automatically normalized — transport envelopes "
        "(status/result/content wrappers, content-block arrays) are stripped. "
        "If ``suggested_output_schema`` is present it is a learned (inferred) "
        "shape and may be low-confidence: check ``x-confidence`` and "
        "``x-observation_count`` before relying on it; below the configured "
        "agent-injection threshold (default 0.8) it is shown here on request "
        "but not on tools/list."
    )

    def _get_suggested_output_schema(self, mcp: str, tool: str) -> dict[str, Any] | None:
        """Return the learned output schema for ``mcp__tool`` if available.

        F-088 · T-890. Returns ``None`` when no schema store is wired or no
        learned schema exists yet for this tool. Never raises -- surfacing is
        best-effort.
        """
        if self._schema_store is None:
            return None
        try:
            suggested = self._schema_store.get(mcp, tool)
            if suggested is None:
                return None
            from ploston_core.schema import format_inferred_schema

            return format_inferred_schema(suggested)
        except Exception:
            return None

    def _compose_schema_response(
        self,
        *,
        mcp: str,
        tool: str,
        runner: str | None,
        description: str | None,
        input_schema: dict[str, Any],
        declared_output_schema: dict[str, Any] | None,
        suggested: dict[str, Any] | None,
        source: str,
        response_hint: str,
    ) -> dict[str, Any]:
        """Shape a single resolved-tool response, including learned output schema.

        Separates declared ``output_schema`` (from tool metadata) and
        ``suggested_output_schema`` (F-088 learned) so agents can distinguish
        authoritative vs. inferred shapes.
        """
        payload: dict[str, Any] = {
            "mcp_server": mcp,
            "tool": tool,
            "runner": runner,
            "description": description,
            "input_schema": input_schema,
            "output_schema": declared_output_schema,
            "source": source,
            "response_hint": response_hint,
        }
        if suggested is not None:
            payload["suggested_output_schema"] = suggested
        return payload

    def _resolve_tool_schema(self, mcp: str, tool: str) -> dict[str, Any]:
        """Resolve a single tool's schema via CP-first, then runner-hosted lookup.

        Returns a dict with either the resolved schema (``found`` implicit via
        presence of ``source``/``input_schema``) or a not-found payload with a
        discovery hint. Shared by single and batch code paths.
        """
        response_hint = self._TOOL_SCHEMA_RESPONSE_HINT

        suggested = self._get_suggested_output_schema(mcp, tool)

        # Step 1: CP-direct -- ToolRegistry lookup by server_name.
        # Covers mcp: system (python_exec), mcp: github (CP-registered), etc.
        if self._tool_registry:
            cp_tools = self._tool_registry.list_tools(server_name=mcp)
            for tool_def in cp_tools:
                if tool_def.name == tool:
                    declared_output = getattr(tool_def, "output_schema", None)
                    return self._compose_schema_response(
                        mcp=mcp,
                        tool=tool,
                        runner=None,
                        description=tool_def.description,
                        input_schema=tool_def.input_schema or {},
                        declared_output_schema=declared_output,
                        suggested=suggested,
                        source="cp",
                        response_hint=response_hint,
                    )

        # Step 2: Runner-hosted -- RunnerRegistry lookup.
        if self._runner_registry:
            canonical = f"{mcp}__{tool}"
            for runner in self._runner_registry.list():
                for entry in runner.available_tools or []:
                    if isinstance(entry, str):
                        if entry == canonical:
                            return self._compose_schema_response(
                                mcp=mcp,
                                tool=tool,
                                runner=runner.name,
                                description=None,
                                input_schema={},
                                declared_output_schema=None,
                                suggested=suggested,
                                source="runner",
                                response_hint=response_hint,
                            )
                    elif isinstance(entry, dict):
                        if entry.get("name") == canonical:
                            return self._compose_schema_response(
                                mcp=mcp,
                                tool=tool,
                                runner=runner.name,
                                description=entry.get("description"),
                                input_schema=entry.get("inputSchema") or {},
                                declared_output_schema=entry.get("outputSchema"),
                                suggested=suggested,
                                source="runner",
                                response_hint=response_hint,
                            )

        # Not found -- return structured error with discovery hint (S-280 R1:
        # dropped embedded available_tools; callers should use workflow_list_tools).
        return {
            "found": False,
            "mcp_server": mcp,
            "tool": tool,
            "error": (
                f"Tool '{tool}' not found on MCP server '{mcp}'. "
                "Use workflow_list_tools to discover available tools."
            ),
            "hint": "Call workflow_list_tools to list available tools by MCP server.",
            "response_hint": response_hint,
        }

    async def _handle_tool_schema(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_tool_schema tool call.

        Supports two modes:
        - Single: ``{"mcp": ..., "tool": ...}`` → resolved schema dict.
        - Batch (S-279): ``{"tools": [{"mcp": ..., "tool": ...}, ...]}`` →
          ``{"results": [...]}`` one entry per request preserving order.
        """
        tools_list = arguments.get("tools")
        if tools_list is not None:
            # Batch mode. Empty list is treated as a valid no-op.
            results: list[dict[str, Any]] = []
            for entry in tools_list:
                if not isinstance(entry, dict):
                    raise create_error(
                        "PARAM_INVALID",
                        message="each item in 'tools' must be an object with 'mcp' and 'tool'",
                    )
                mcp = entry.get("mcp")
                tool = entry.get("tool")
                if not mcp or not tool:
                    raise create_error(
                        "PARAM_INVALID",
                        message="each item in 'tools' requires non-empty 'mcp' and 'tool'",
                    )
                results.append(self._resolve_tool_schema(mcp, tool))
            return {"results": results}

        mcp = arguments.get("mcp")
        tool = arguments.get("tool")
        if not mcp:
            raise create_error("PARAM_INVALID", message="'mcp' parameter is required")
        if not tool:
            raise create_error("PARAM_INVALID", message="'tool' parameter is required")
        return self._resolve_tool_schema(mcp, tool)

    async def _handle_call_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_call_tool tool call (S-278 / DEC-185).

        Resolves the tool (CP-direct first, then runner-hosted) and invokes it
        via ToolInvoker. The response payload is normalized with
        normalize_mcp_response so callers see the same shape that
        ``context.steps[id].output`` would carry inside a workflow step.
        """
        mcp = arguments.get("mcp")
        tool = arguments.get("tool")
        params = arguments.get("params") or {}

        if not mcp:
            raise create_error("PARAM_INVALID", message="'mcp' parameter is required")
        if not tool:
            raise create_error("PARAM_INVALID", message="'tool' parameter is required")
        if not isinstance(params, dict):
            raise create_error("PARAM_INVALID", message="'params' must be an object when provided")

        if self._tool_invoker is None:
            raise create_error(
                "INTERNAL",
                message="workflow_call_tool is unavailable: ToolInvoker not configured",
            )

        # Resolve invocation name using the same CP-first order as
        # _handle_tool_schema. First-match wins when multiple runners expose
        # the same canonical name — mirrors the schema resolver's behavior.
        invoke_name: str | None = None
        runner_name: str | None = None
        source: str | None = None

        if self._tool_registry:
            for tool_def in self._tool_registry.list_tools(server_name=mcp):
                if tool_def.name == tool:
                    invoke_name = tool  # CP-direct: bare tool name
                    source = "cp"
                    break

        if invoke_name is None and self._runner_registry:
            canonical = f"{mcp}__{tool}"
            for runner in self._runner_registry.list():
                for entry in runner.available_tools or []:
                    entry_name = entry if isinstance(entry, str) else entry.get("name")
                    if entry_name == canonical:
                        invoke_name = f"{runner.name}__{canonical}"
                        runner_name = runner.name
                        source = "runner"
                        break
                if invoke_name is not None:
                    break

        if invoke_name is None:
            return {
                "success": False,
                "mcp_server": mcp,
                "tool": tool,
                "error": (
                    f"Tool '{tool}' not found on MCP server '{mcp}'. "
                    "Use workflow_list_tools to discover available tools."
                ),
                "hint": "Call workflow_list_tools to list available tools by MCP server.",
            }

        try:
            result = await self._tool_invoker.invoke(invoke_name, params)
        except AELError as e:
            return {
                "success": False,
                "mcp_server": mcp,
                "tool": tool,
                "runner": runner_name,
                "source": source,
                "error": str(e),
                "error_code": getattr(e, "code", None),
            }

        if not result.success:
            return {
                "success": False,
                "mcp_server": mcp,
                "tool": tool,
                "runner": runner_name,
                "source": source,
                "error": str(result.error) if result.error else "tool call failed",
                "duration_ms": result.duration_ms,
            }

        return {
            "success": True,
            "mcp_server": mcp,
            "tool": tool,
            "runner": runner_name,
            "source": source,
            "output": normalize_mcp_response(result.output),
            "duration_ms": result.duration_ms,
        }

    async def _handle_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_run tool call.

        Executes a workflow via WorkflowEngine.execute() — same path as
        direct MCP workflow calls for telemetry consistency (DEC-171).
        """
        name = arguments.get("name")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")

        if not self._workflow_engine:
            raise create_error(
                "INTERNAL",
                message="workflow_run is unavailable: WorkflowEngine not configured",
            )

        inputs = arguments.get("inputs") or {}
        result = await self._workflow_engine.execute(name, inputs)

        # S-271 / T-864: emit the centralized MCP response shape so step-level
        # telemetry (debug_log, duration_ms, per-step status) is surfaced to
        # the workflow_run caller. Top-level "result" key preserves the prior
        # contract; "execution" and "workflow_version" are additive.
        return result.to_mcp_response()


def _to_json(data: Any) -> str:
    """Serialize data to JSON string."""
    return json.dumps(data, default=str)
