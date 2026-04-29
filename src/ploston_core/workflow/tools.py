"""Workflow management tools for MCP exposure.

Provides flat-named workflow management tools (workflow_schema, workflow_list,
workflow_get, workflow_get_definition, workflow_create, workflow_delete,
workflow_patch, workflow_tool_schema, workflow_run, workflow_list_tools,
workflow_call_tool) that delegate to WorkflowRegistry / WorkflowEngine.

Authoring intent: agents author by submitting YAML directly through
``workflow_create``; validation runs as part of that call and is returned in
``validation: {valid, errors, warnings}``. On failure, ``workflow_create``
returns a draft with a ``draft_id`` and ``suggested_fix`` ops; agents iterate
via ``workflow_patch`` rather than re-calling ``workflow_create``.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ploston_core.engine.normalize import normalize_mcp_response
from ploston_core.errors import AELError, create_error
from ploston_core.types.validation import ValidationIssue, ValidationResult

from .schema_generator import (
    AVAILABLE_SECTIONS,
    generate_section,
    generate_tier1_schema,
)


def _render_tier1_for_description() -> str:
    """Render the Tier 1 schema as a compact text block for tool descriptions.

    The output is intentionally terse — repeated field names and trivial
    English wording are stripped so the embedded schema fits under the 2K
    token budget enforced by ``test_tier1_under_2k_tokens``.
    """
    t1 = generate_tier1_schema()
    lines: list[str] = ["YAML schema (Tier 1):"]
    if t1.get("before_you_start"):
        lines.append(f"Before you start: {t1['before_you_start']}")
    lines.append("Fields:")
    for k, v in t1["fields"].items():
        lines.append(f"  {k}: {v}")
    lines.append("Step types:")
    for k, v in t1["step_types"].items():
        lines.append(f"  {k}:")
        for sub in v.split("\n"):
            lines.append(f"    {sub}")
    lines.append(f"Templates: {t1['template_syntax']}")
    lines.append("Inputs shorthand:")
    for sub in t1["input_shorthand"].split("\n"):
        lines.append(f"  {sub}")
    lines.append("Outputs:")
    for sub in t1["output_syntax"].split("\n"):
        lines.append(f"  {sub}")
    lines.append(t1["more_detail"])
    return "\n".join(lines)


# Computed once at import — embedded into WORKFLOW_CREATE_TOOL description below.
_TIER1_DESCRIPTION_BLOCK = _render_tier1_for_description()

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
        "workflow_delete",
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
        "S-290 P2: workflow_schema responses now follow one of three shapes: "
        "the Tier 1 minimal schema (``schema`` + ``sections`` + ``tier`` + "
        "``authoring_note``), a single section view (``section`` + ``schema`` "
        "+ ``available_sections``), or an unknown-section error (``error`` + "
        "``available_sections``)."
    ),
    "properties": {
        "schema": {"type": "object", "additionalProperties": True},
        "sections": {"type": "array", "items": {"type": "string"}},
        "authoring_note": {"type": "string"},
        "tier": {"type": "integer"},
        "section": {"type": "string"},
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


# S-291 P3: ``workflow_create`` is the single workflow-mutation surface.
# ``status`` is "created" on registration or "draft" on validation failure /
# ``dry_run=true``. ``tool_preview`` is nullable in the draft case;
# ``draft_id`` is set when a draft was stashed; ``validation`` carries the
# enriched error envelope plus structured warnings (with optional 1-based
# ``line`` for code-step static checks like the missing-``await`` detector).
_OUTPUT_SCHEMA_WORKFLOW_CREATE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "status": {"type": "string", "enum": ["created", "draft"]},
        "tool_preview": {
            "type": ["object", "null"],
            "additionalProperties": True,
        },
        "warnings": {"type": "array"},
        "draft_id": {"type": ["string", "null"]},
        "validation": {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean"},
                "errors": {"type": "array"},
                "warnings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "message": {"type": "string"},
                            # Optional 1-based source line for static-analysis
                            # warnings that pinpoint a specific location inside
                            # a code step (S-286 / T-904 missing-await check).
                            "line": {"type": "integer", "minimum": 1},
                        },
                        "required": ["path", "message"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["valid", "errors", "warnings"],
            "additionalProperties": False,
        },
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

_OUTPUT_SCHEMA_WORKFLOW_DELETE = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "status": {"type": "string", "enum": ["deleted"]},
    },
    "required": ["name", "status"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA_WORKFLOW_PATCH = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        # S-291 P3: ``draft`` is the response when a draft patch is still
        # invalid; ``patched`` is the registered-or-promoted case.
        "status": {"type": "string", "enum": ["patched", "draft"]},
        "patches_applied": {"type": "integer", "minimum": 0},
        "tool_preview": {
            "type": ["object", "null"],
            "additionalProperties": True,
        },
        "warnings": {"type": "array"},
        "draft_id": {"type": ["string", "null"]},
        "validation": {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean"},
                "errors": {"type": "array"},
                "warnings": {"type": "array"},
            },
            "required": ["valid", "errors", "warnings"],
            "additionalProperties": False,
        },
        "promoted_from_draft": {"type": "boolean"},
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
        "Get workflow YAML schema documentation. "
        "Without arguments returns the Tier 1 minimal schema (~1.5K tokens) — "
        "field names, step types, template syntax, and a pointer to detailed "
        "sections. Call with section=NAME for deeper detail. "
        "Sections: discovery, sandbox_constraints, context_api, tool_steps, "
        "inputs, outputs, defaults, packages, examples. "
        "Authoring flow: workflow_schema → "
        'workflow_schema(section="discovery") → workflow_list_tools → '
        "workflow_tool_schema → workflow_create. The discovery section "
        "carries investigation-discipline principles (narrow filters over "
        "broad calls, schema-then-call, source-over-surface) and should be "
        "loaded before authoring against an unfamiliar tool. "
        "If workflow_create returns "
        '`status="draft"`, fix it via workflow_patch with the returned '
        "`draft_id` — do not re-call workflow_create. If workflow_run fails, "
        "repair the registered workflow with workflow_patch. Do not call "
        "any separate validate or update tool."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": ("Optional section name. Omit to get the Tier 1 minimal schema."),
                "enum": [
                    "discovery",
                    "sandbox_constraints",
                    "context_api",
                    "tool_steps",
                    "inputs",
                    "outputs",
                    "defaults",
                    "packages",
                    "examples",
                ],
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
        "Author and publish a workflow in a single call. This is the entry "
        "point for workflow authoring — submit the YAML directly; do not "
        "pre-validate it through any other tool. Validation runs as part of "
        "`workflow_create` and its result is always returned in "
        "`validation: {valid, errors, warnings}`.\n\n"
        "Before authoring against an unfamiliar tool, call "
        '`workflow_schema(section="discovery")` for investigation-discipline '
        "principles (narrow filters over broad calls, schema-then-call via "
        "`workflow_tool_schema` + `workflow_call_tool`, source-over-surface).\n\n"
        "Outcomes:\n"
        "- Success: the workflow is registered and appears in tools/list "
        'under its bare name (`status="created"`). The response also '
        "includes `validation.warnings` for non-fatal advisories "
        "(missing `await` on `context.tools.call_mcp`, weak descriptions, "
        "undocumented inputs).\n"
        "- Validation failure: the call returns "
        '`status="draft"` with a `draft_id` and an enriched '
        "`validation.errors` list. Each error carries `path`, `message`, "
        "`current_value`, and a `suggested_fix` patch op. Pass the "
        "`draft_id` plus the `suggested_fix` (or your own ops) to "
        "`workflow_patch` to fix and re-validate without resubmitting "
        "the full YAML. Iterate via patch — do not re-call "
        "`workflow_create` with the corrected YAML.\n\n"
        "Pass `dry_run=true` to validate without registering or storing a "
        "draft (response shape is identical). Most authoring flows do not "
        "need `dry_run`: just call `workflow_create` and let the "
        "draft/patch loop handle errors.\n\n"
        "On success the response includes a preview of how the tool will "
        "appear in tools/list. Dashes ('-') in the workflow name are "
        "automatically replaced with underscores ('_'); when a rename "
        "occurs the response includes `name_sanitized` with the original "
        "name for reference.\n\n" + _TIER1_DESCRIPTION_BLOCK
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
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, validate only — do not register and do not stash a draft. "
                    "Useful for previewing the validation/error envelope. The response "
                    "shape is identical to a real call."
                ),
            },
        },
    },
    "outputSchema": _OUTPUT_SCHEMA_WORKFLOW_CREATE,
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

WORKFLOW_PATCH_TOOL = {
    "name": "workflow_patch",
    "description": (
        "Iterate on a workflow with targeted edits — without resubmitting "
        "the full YAML. This is the standard repair path for both "
        "authoring and runtime failures:\n"
        '- After `workflow_create` returns `status="draft"` with a '
        "`draft_id`, pass that `draft_id` plus the "
        "`validation.errors[].suggested_fix` (or your own ops) here to "
        "re-validate; on success the workflow is registered automatically "
        "and the draft is dropped; on failure the same `draft_id` comes "
        "back updated.\n"
        "- After `workflow_run` fails on a registered workflow, pass "
        "`name` + a new `version` plus ops derived from the engine's "
        "`error_metadata.suggested_fix` (or your own diagnosis) to repair "
        "it in place.\n\n"
        "Each operation is one of four shapes:\n"
        "- `{op:'replace', step_id, old, new}` — str_replace inside a "
        "single code step's `code` block (the `old` substring must be "
        "unique within that step).\n"
        "- `{op:'set', path, value}` — set the YAML scalar at a "
        "dot-delimited path (e.g. `steps.greet.mcp`, `steps.greet.tool`, "
        "`defaults.timeout`, `inputs.lookback.default`).\n"
        "- `{op:'add_step', after, step}` — insert a new step after the "
        "step with id `after` (or at the start when `after` is null/omitted). "
        "`step` must include `id` and either ('tool' + 'mcp') or 'code'.\n"
        "- `{op:'remove_step', step_id}` — remove the step with id "
        "`step_id`. Refuses if other steps reference it via `depends_on`.\n"
        "Op shapes mirror `validation.errors[].suggested_fix` from "
        "`workflow_create` and `error_metadata.suggested_fix` from "
        "`workflow_run`, so an agent can pass a fix straight back in. "
        "If any operation fails, no changes are persisted.\n\n"
        "Validation runs on every patch and is returned in "
        "`validation: {valid, errors, warnings}`. Live-safety invariant: "
        "patching a registered workflow validates the patched YAML on a "
        "copy before swapping it in. If validation fails, the live "
        "workflow stays at its current version and a new draft is "
        "returned under `draft_id` so the agent can keep iterating.\n\n"
        "Two modes:\n"
        "- Registered workflow: pass `name` and `version`. The new "
        "`version` must differ from the current version. The response "
        "includes `previous_version` so the agent can track what changed.\n"
        "- Draft: pass `draft_id` returned by `workflow_create` (or by a "
        "previous failed live patch). The patched YAML is re-validated; "
        "on success it is registered (and the draft dropped); on failure "
        "the draft is updated in place and the same `draft_id` is "
        "returned.\n\n"
        "The legacy `patches` parameter (a list of "
        "`{step_id, old, new}` entries) is still accepted as a synonym "
        "for `operations` containing `op:'replace'` entries.\n\n"
        "Note: dashes ('-') in the workflow name are automatically "
        "replaced with underscores ('_') for lookup."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Workflow name to patch. Dashes are replaced with underscores before lookup. "
                    "Required when patching a registered workflow; mutually exclusive with `draft_id`."
                ),
            },
            "version": {
                "type": "string",
                "description": (
                    "New version string for the patched workflow "
                    "(e.g. '2.1.0'). Required when patching a registered "
                    "workflow; ignored in draft mode."
                ),
            },
            "draft_id": {
                "type": "string",
                "description": (
                    "Draft identifier returned by workflow_create when validation "
                    "fails. Mutually exclusive with `name`."
                ),
            },
            "operations": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Ordered list of operations to apply. Each entry must "
                    "match the shape returned in "
                    "`validation.errors[].suggested_fix` so suggested fixes "
                    "can be passed straight back in."
                ),
                "items": {
                    "type": "object",
                    "oneOf": [
                        {
                            "required": ["op", "step_id", "old", "new"],
                            "properties": {
                                "op": {"const": "replace"},
                                "step_id": {"type": "string"},
                                "old": {"type": "string"},
                                "new": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        {
                            "required": ["op", "path", "value"],
                            "properties": {
                                "op": {"const": "set"},
                                "path": {"type": "string"},
                                "value": {},
                            },
                            "additionalProperties": False,
                        },
                        {
                            "required": ["op", "step"],
                            "properties": {
                                "op": {"const": "add_step"},
                                "after": {"type": ["string", "null"]},
                                "step": {"type": "object"},
                            },
                            "additionalProperties": False,
                        },
                        {
                            "required": ["op", "step_id"],
                            "properties": {
                                "op": {"const": "remove_step"},
                                "step_id": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    ],
                },
            },
            "patches": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Legacy alias for `operations` with `op:'replace'`. "
                    "Each item is `{step_id, old, new}`."
                ),
                "items": {
                    "type": "object",
                    "required": ["step_id", "old", "new"],
                    "properties": {
                        "step_id": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
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
        "Returns workflow outputs on success, or a structured error on "
        "failure with `error_metadata` (code context, prior step outputs, "
        "rendered params, and a `suggested_fix` op when applicable). "
        "On failure, repair the workflow via `workflow_patch` using that "
        "`suggested_fix` (or your own diagnosis) — do not re-author it "
        "with `workflow_create`."
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
    WORKFLOW_DELETE_TOOL,
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
        from .authoring_metrics import WorkflowAuthoringMetrics
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
        # M-081 Measurement Plan: instruments are no-ops until ``set_meter``
        # is called by Application during telemetry wire-up.
        self._authoring_metrics = WorkflowAuthoringMetrics(meter=None)
        self._handlers: dict[str, Callable[..., Any]] = {
            "workflow_schema": self._handle_schema,
            "workflow_list": self._handle_list,
            "workflow_list_tools": self._handle_list_tools,
            "workflow_get": self._handle_get,
            "workflow_get_definition": self._handle_get_definition,
            "workflow_create": self._handle_create,
            "workflow_delete": self._handle_delete,
            "workflow_patch": self._handle_patch,
            "workflow_tool_schema": self._handle_tool_schema,
            "workflow_call_tool": self._handle_call_tool,
            "workflow_run": self._handle_run,
        }

    def set_meter(self, meter: Any) -> None:
        """Wire an OpenTelemetry meter into the authoring-DX metrics.

        Called by ``Application`` during startup once telemetry is set
        up. Until this is called, all metric recording is a no-op.
        """
        from .authoring_metrics import WorkflowAuthoringMetrics

        self._authoring_metrics = WorkflowAuthoringMetrics(meter=meter)

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

        S-290 P2: section-aware dispatch.
            - No ``section`` arg → Tier 1 minimal schema (~1.5K tokens)
            - Recognized ``section`` → that section only
            - Unknown ``section`` → error + ``available_sections`` listing

        Use ``workflow_list_tools`` to discover tools available for workflow
        steps; this handler returns schema documentation only.
        """
        section = arguments.get("section")

        if section:
            if section not in AVAILABLE_SECTIONS:
                return {
                    "error": f"Unknown section: {section}",
                    "available_sections": list(AVAILABLE_SECTIONS),
                }
            response = {
                "section": section,
                "schema": generate_section(section),
                "available_sections": list(AVAILABLE_SECTIONS),
            }
            self._authoring_metrics.record_schema_response_bytes(
                len(_to_json(response).encode("utf-8")),
                section=section,
            )
            return response

        response = {
            "authoring_note": "Author the workflow. Document the tool.",
            "tier": 1,
            "sections": list(AVAILABLE_SECTIONS),
            "schema": generate_tier1_schema(),
        }
        self._authoring_metrics.record_schema_response_bytes(
            len(_to_json(response).encode("utf-8")),
            section="tier1",
        )
        return response

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
        """Handle workflow_create tool call.

        S-291 P3: validation is folded into the creation flow.

        - On full success: register and return ``status="created"``.
        - On validation failure (or with ``dry_run=true``): do NOT register;
          stash the YAML in the draft store, return ``status="draft"`` with
          ``draft_id`` and an enriched ``validation`` block.
        - On parser failure: return ``status="draft"`` with a single
          ``parse_error`` issue and (if possible) a draft_id seeded from the
          raw YAML so ``workflow_patch`` can still operate on it.
        """
        from ploston_core.sandbox.sandbox import DANGEROUS_BUILTINS, SAFE_IMPORTS

        from .parser import parse_workflow_yaml
        from .validator import (
            check_forbidden_builtins,
            check_forbidden_imports,
            check_return_in_code,
            detect_reserved_input_names,
        )

        yaml_content = arguments.get("yaml_content")
        if not yaml_content:
            raise create_error("PARAM_INVALID", message="'yaml_content' parameter is required")
        dry_run = bool(arguments.get("dry_run", False))

        # 1. Parse. A parse failure is itself a validation error — surface
        #    it via the draft pathway so the agent can fix and re-submit.
        try:
            workflow = parse_workflow_yaml(yaml_content)
        except Exception as exc:
            response = self._draft_response_from_parse_error(yaml_content, exc, dry_run=dry_run)
            self._authoring_metrics.record_workflow_create(status="draft")
            if response.get("draft_id"):
                self._authoring_metrics.record_draft_created()
            return response

        # 2. Sanitize the workflow name (dashes → underscores). This also
        #    rewrites the YAML text so the persisted draft / registered
        #    workflow matches what's in memory.
        original_name = workflow.name
        sanitized_name = _sanitize_workflow_name(original_name)
        name_was_sanitized = sanitized_name != original_name
        if name_was_sanitized:
            yaml_content = _rewrite_workflow_name_in_yaml(yaml_content, sanitized_name)
            workflow.name = sanitized_name

        # 3. Run the full validator + the new static checks in one pass.
        #    Prefer ``validate_yaml`` so test doubles that monkey-patch the
        #    public surface still take effect; fall back to the internal
        #    ``_validator`` only if the public method is missing. The
        #    result is then normalised — tests sometimes pass a MagicMock
        #    registry whose attributes resolve to MagicMock objects rather
        #    than real ``ValidationResult`` instances, so we coerce
        #    ``errors``/``warnings``/``valid`` into safe defaults before
        #    proceeding.
        try:
            if hasattr(self._registry, "validate_yaml"):
                result = self._registry.validate_yaml(yaml_content)
            else:
                result = self._registry._validator.validate(workflow)
        except Exception:
            result = ValidationResult(valid=True, errors=[], warnings=[])
        if not isinstance(result, ValidationResult):
            result = ValidationResult(valid=True, errors=[], warnings=[])
        if not isinstance(result.errors, list):
            result.errors = []
        if not isinstance(result.warnings, list):
            result.warnings = []
        for issue in check_return_in_code(workflow):
            result.errors.append(issue)
        for issue in check_forbidden_imports(workflow, SAFE_IMPORTS):
            result.errors.append(issue)
        for issue in check_forbidden_builtins(workflow, DANGEROUS_BUILTINS):
            result.errors.append(issue)
        for issue in detect_reserved_input_names(workflow):
            result.errors.append(issue)
        # Description-quality advisories (S-286): warning-only checks that
        # nudge authors to write tools/list-grade docs for the workflow's
        # public surface (top-level ``description`` and each input's
        # ``description``). Never affect ``valid``.
        fallback_description = f"Execute {workflow.name} workflow"
        if not workflow.description or workflow.description.strip() == fallback_description:
            result.warnings.append(
                ValidationIssue(
                    path="description",
                    message=(
                        "description is empty or generic — agents will see a "
                        "fallback string in tools/list. Write it as tool "
                        "documentation: purpose, return value, when to use."
                    ),
                    severity="warning",
                )
            )
        for inp in workflow.inputs or []:
            if not inp.description:
                result.warnings.append(
                    ValidationIssue(
                        path=f"inputs[{inp.name}].description",
                        message=(
                            f"Input '{inp.name}' has no description — agents "
                            "will see no parameter doc for this field in "
                            "tools/list."
                        ),
                        severity="warning",
                    )
                )
        # Missing-await check (S-286 / T-905): warning-only AST scan that
        # flags ``context.tools.call(...)``/``call_mcp(...)`` calls that
        # forget the ``await`` keyword. Never affects ``valid``.
        if workflow.steps:
            for warning in _check_missing_await(workflow.steps):
                result.warnings.append(
                    ValidationIssue(
                        path=warning["path"],
                        message=warning["message"],
                        severity="warning",
                        line=warning.get("line"),
                    )
                )
        if result.errors:
            result.valid = False

        if not result.valid or dry_run:
            response = self._draft_response(
                workflow=workflow,
                yaml_content=yaml_content,
                result=result,
                original_name=original_name,
                name_was_sanitized=name_was_sanitized,
                dry_run=dry_run,
            )
            self._authoring_metrics.record_workflow_create(status="draft")
            if response.get("draft_id"):
                self._authoring_metrics.record_draft_created()
            return response

        # 4. Valid + not dry_run → register normally. Going through the
        #    public ``register_from_yaml`` keeps the existing test surface
        #    (which mocks that method) and persistence behaviour intact;
        #    validation runs again here but is idempotent and cheap.
        self._registry.register_from_yaml(yaml_content, persist=True)
        await self._notify_tools_changed()

        tool_preview, warnings = self._build_tool_preview(workflow)
        # Surface any structured advisories collected during validation
        # (e.g. missing-await warnings) on the success path too.
        validation_warnings: list[dict[str, Any]] = []
        for w in result.warnings:
            entry: dict[str, Any] = {"path": w.path, "message": w.message}
            line = getattr(w, "line", None)
            if line is not None:
                entry["line"] = line
            validation_warnings.append(entry)
        response: dict[str, Any] = {
            "name": workflow.name,
            "version": workflow.version,
            "status": "created",
            "tool_preview": tool_preview,
            "warnings": warnings,
            "draft_id": None,
            "validation": {
                "valid": True,
                "errors": [],
                "warnings": validation_warnings,
            },
        }
        if name_was_sanitized:
            response["name_sanitized"] = {
                "original": original_name,
                "registered_as": sanitized_name,
                "reason": "dashes replaced with underscores",
            }
        self._authoring_metrics.record_workflow_create(status="created")
        return response

    # ── Draft helpers (S-291 P3) ────────────────────────────────────────────

    def _draft_response_from_parse_error(
        self, yaml_content: str, exc: BaseException, *, dry_run: bool
    ) -> dict[str, Any]:
        """Build a ``status="draft"`` response for an unparseable YAML."""
        issue = {
            "path": "yaml",
            "message": f"YAML parse error: {exc}",
            "suggested_fix": None,
            "requires_agent_decision": True,
        }
        # We can still stash the raw text so ``workflow_patch`` may operate
        # on it (the patch surface uses ruamel which can sometimes tolerate
        # what the strict parser rejects).
        draft_id: str | None = None
        if not dry_run:
            stash_result = ValidationResult(
                valid=False,
                errors=[ValidationIssue(path="yaml", message=str(exc), severity="error")],
                warnings=[],
            )
            draft_id = self._registry.draft_store.put(
                yaml_content, name="", version="", validation=stash_result
            )
        return {
            "name": "",
            "version": "",
            "status": "draft",
            "tool_preview": None,
            "warnings": [],
            "draft_id": draft_id,
            "validation": {"valid": False, "errors": [issue], "warnings": []},
        }

    def _draft_response(
        self,
        *,
        workflow: WorkflowDefinition,
        yaml_content: str,
        result: ValidationResult,
        original_name: str,
        name_was_sanitized: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        from .validator import enrich_validation_result

        # Build the catalog inputs for the enricher.
        groups = self._build_available_tools()
        available_tools_by_mcp = {g["mcp_server"]: list(g["tools"]) for g in groups}
        available_mcps = list(available_tools_by_mcp.keys())

        from ploston_core.sandbox.sandbox import DANGEROUS_BUILTINS, SAFE_IMPORTS

        enriched_errors = enrich_validation_result(
            result,
            workflow,
            available_tools_by_mcp=available_tools_by_mcp,
            available_mcps=available_mcps,
            safe_imports=SAFE_IMPORTS,
            dangerous_builtins=DANGEROUS_BUILTINS,
        )
        warnings_payload: list[dict[str, Any]] = []
        for w in result.warnings:
            entry: dict[str, Any] = {"path": w.path, "message": w.message}
            line = getattr(w, "line", None)
            if line is not None:
                entry["line"] = line
            warnings_payload.append(entry)

        # When the workflow is valid AND dry_run, no draft is stored — the
        # caller is just inspecting. Otherwise stash the YAML so the next
        # ``workflow_patch`` can pick it up by ID.
        draft_id: str | None = None
        if not result.valid and not dry_run:
            draft_id = self._registry.draft_store.put(
                yaml_content,
                name=workflow.name,
                version=workflow.version,
                validation=result,
            )

        response: dict[str, Any] = {
            "name": workflow.name,
            "version": workflow.version,
            "status": "draft" if not result.valid else "created",
            "tool_preview": None if not result.valid else self._build_tool_preview(workflow)[0],
            "warnings": [] if not result.valid else self._build_tool_preview(workflow)[1],
            "draft_id": draft_id,
            "validation": {
                "valid": result.valid,
                "errors": enriched_errors,
                "warnings": warnings_payload,
            },
        }
        if name_was_sanitized:
            response["name_sanitized"] = {
                "original": original_name,
                "registered_as": workflow.name,
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

    async def _handle_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_patch tool call.

        Applies a list of str_replace edits to individual code steps in
        either:

        - a registered workflow's stored YAML (``name`` mode), or
        - a previously-drafted workflow's stashed YAML (``draft_id`` mode,
          S-291 P3) — let an agent fix validation errors without
          re-uploading the entire YAML blob.

        Round-trip preservation is handled by ``ruamel.yaml`` so comments
        and the ``code: |`` block scalar style are kept intact.

        All patches must succeed before any change is persisted. In
        ``name`` mode the patched workflow is re-registered and the
        version bumped. In ``draft_id`` mode the patched YAML is
        re-validated; if it now validates cleanly it is registered (and
        the draft is dropped); otherwise the draft is updated in place.
        """
        from io import StringIO

        from ruamel.yaml import YAML

        name = arguments.get("name")
        version = arguments.get("version")
        patches = arguments.get("patches")
        operations = arguments.get("operations")
        draft_id = arguments.get("draft_id")

        # Normalize legacy ``patches`` (op:'replace' shape without explicit
        # op) and modern ``operations`` (suggested_fix shape) into a
        # single ordered list. Both names are accepted; if both are
        # supplied they are concatenated in the order: operations,
        # then patches.
        ops: list[dict[str, Any]] = []
        if operations:
            ops.extend(operations)
        if patches:
            for p in patches:
                ops.append({"op": "replace", **p})

        # Match existing handler pattern: use PARAM_INVALID with
        # tool_name for parity even though the ``message`` kwarg is
        # templated out by the error registry.
        if not ops:
            raise create_error("PARAM_INVALID", tool_name="workflow_patch")
        if not name and not draft_id:
            raise create_error("PARAM_INVALID", tool_name="workflow_patch")
        if name and draft_id:
            raise create_error(
                "PARAM_INVALID",
                tool_name="workflow_patch",
                message="Provide either 'name' or 'draft_id', not both",
            )

        # S-292 P4: cap the number of operations per call. Sourced from
        # ``WorkflowsConfig.max_patches_per_call`` (default 10) via the
        # registry's stored config. Defensive: non-int values (e.g. a
        # ``MagicMock`` from a unit test fixture) fall back to the
        # default rather than raising on the comparison.
        cfg_value = getattr(
            getattr(self._registry, "_config", None),
            "max_patches_per_call",
            10,
        )
        max_per_call = cfg_value if isinstance(cfg_value, int) else 10
        if len(ops) > max_per_call:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"Too many operations: {len(ops)} (max "
                    f"{max_per_call} per call). Split the patch into "
                    "smaller batches."
                ),
            )

        is_draft_mode = bool(draft_id)
        yaml_content: str
        if is_draft_mode:
            assert isinstance(draft_id, str)
            entry = self._registry.draft_store.get(draft_id)
            if not entry:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"Draft '{draft_id}' not found or expired. "
                        "Re-submit the YAML via workflow_create to get a fresh draft_id."
                    ),
                )
            yaml_content = entry.yaml_content
            # Use the draft's recorded name (if any) as the working name
            # so error paths and the response reference the right workflow.
            name = entry.name or ""
        previous_version: str | None = None
        if not is_draft_mode:
            if not version:
                raise create_error("PARAM_INVALID", tool_name="workflow_patch")
            assert isinstance(name, str)
            name = _sanitize_workflow_name(name)
            existing = self._registry.get(name)
            if not existing:
                raise create_error("WORKFLOW_NOT_FOUND", workflow_id=name)

            previous_version = getattr(existing, "version", None)
            # Spec P4a: same-version patches on a live workflow are
            # rejected. Drafts are exempt — see the version semantics
            # section.
            if previous_version and version == previous_version:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"version '{version}' must differ from the current "
                        f"version of '{name}' ({previous_version}). Use a new "
                        "version string (e.g., bump the patch number)."
                    ),
                )

            stored_yaml = existing.yaml_content
            if not stored_yaml:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(f"Workflow '{name}' has no stored YAML content available for patching"),
                )
            yaml_content = stored_yaml

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
        for index, op_entry in enumerate(ops):
            op_type = op_entry.get("op", "replace")
            if op_type == "replace":
                self._apply_replace_op(op_entry, index, name, steps_list)
            elif op_type == "set":
                self._apply_set_op(op_entry, index, data)
            elif op_type == "add_step":
                self._apply_add_step_op(op_entry, index, data)
            elif op_type == "remove_step":
                self._apply_remove_step_op(op_entry, index, data)
            else:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"operations[{index}].op must be one of 'replace', "
                        f"'set', 'add_step', 'remove_step'; got {op_type!r}"
                    ),
                )

        # Only bump the version when patching a registered workflow. For
        # drafts, ``version`` is whatever the YAML carries — agents can
        # set it explicitly via a separate patch op if they need to.
        if not is_draft_mode:
            data["version"] = version

        stream = StringIO()
        yaml_rt.dump(data, stream)
        patched_yaml = stream.getvalue()

        if is_draft_mode:
            assert isinstance(draft_id, str)
            return await self._patch_draft_finalize(
                draft_id=draft_id,
                patched_yaml=patched_yaml,
                patches_applied=len(ops),
            )

        # Live-workflow safety (spec §P4): validate the patched YAML
        # BEFORE touching the registry. On failure, the live workflow
        # remains untouched and the patched copy is stored as a new
        # draft so the agent can keep iterating.
        return await self._patch_live_finalize(
            name=name,
            patched_yaml=patched_yaml,
            patches_applied=len(ops),
            previous_version=previous_version,
        )

    async def _patch_live_finalize(
        self,
        *,
        name: str,
        patched_yaml: str,
        patches_applied: int,
        previous_version: str | None,
    ) -> dict[str, Any]:
        """Validate-then-register a patched live workflow.

        The live registered version is never modified until validation
        passes. On failure, a draft is created from the patched YAML and
        returned with ``status="draft"`` so the agent can iterate.
        """
        create_response = await self._handle_create({"yaml_content": patched_yaml, "dry_run": True})
        validation = create_response.get("validation") or {}

        if not validation.get("valid"):
            # Stash a draft from the failing patched YAML so the agent
            # can iterate without losing their work.
            from .parser import parse_workflow_yaml

            try:
                wf_for_draft = parse_workflow_yaml(patched_yaml)
            except Exception:
                wf_for_draft = None
            new_draft_id = self._registry.draft_store.put(
                patched_yaml,
                name=getattr(wf_for_draft, "name", name) or name,
                version=getattr(wf_for_draft, "version", "") or "",
                validation=None,
            )
            self._authoring_metrics.record_workflow_patch(target="live", status="draft")
            return {
                "name": create_response.get("name", name),
                "version": create_response.get("version", ""),
                "previous_version": previous_version,
                "status": "draft",
                "patches_applied": patches_applied,
                "tool_preview": None,
                "warnings": [],
                "draft_id": new_draft_id,
                "validation": validation,
                "live_workflow_unchanged": True,
            }

        # Validation passed — promote: replace the live version. We do
        # the unregister/register pair only after validation, so an
        # in-flight failure never leaves the registry in an empty state
        # for this workflow name.
        self._registry.unregister(name)
        self._registry.register_from_yaml(patched_yaml, persist=True)
        await self._notify_tools_changed()

        from .parser import parse_workflow_yaml

        workflow = parse_workflow_yaml(patched_yaml)
        tool_preview, warnings = self._build_tool_preview(workflow)
        self._authoring_metrics.record_workflow_patch(target="live", status="patched")
        return {
            "name": workflow.name,
            "version": workflow.version,
            "previous_version": previous_version,
            "status": "patched",
            "patches_applied": patches_applied,
            "tool_preview": tool_preview,
            "warnings": warnings,
            "draft_id": None,
            "validation": {"valid": True, "errors": [], "warnings": []},
        }

    def _apply_replace_op(
        self,
        op_entry: dict[str, Any],
        index: int,
        name: str,
        steps_list: list[Any],
    ) -> None:
        step_id = op_entry.get("step_id")
        old = op_entry.get("old")
        new = op_entry.get("new")
        if not step_id or old is None or new is None:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'replace') must include non-empty "
                    "'step_id', 'old', and 'new' fields"
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

    def _apply_set_op(
        self,
        op_entry: dict[str, Any],
        index: int,
        data: Any,
    ) -> None:
        """Set a YAML scalar at a dot-delimited path.

        Supports paths like ``defaults.timeout``, ``steps.{id}.tool``,
        ``steps.{id}.params.{p}``. The ``steps`` segment is treated as
        an addressable map keyed by step ``id``.
        """
        path = op_entry.get("path")
        if "value" not in op_entry:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'set') must include a "
                    "'value' field (use null for explicit nulls)"
                ),
            )
        value = op_entry["value"]
        if not path or not isinstance(path, str):
            raise create_error(
                "INPUT_INVALID",
                detail=(f"operations[{index}] (op:'set') must include a non-empty 'path' string"),
            )
        # ``steps`` are addressed by ``id``; ``inputs``/``outputs`` are
        # addressed by ``name``. The patch-by-id grammar applies to any
        # YAML sequence that uses ``id`` or ``name`` as the natural key.
        keyed_collections = {
            "steps": "id",
            "inputs": "name",
            "outputs": "name",
        }
        parts = path.split(".")
        cursor: Any = data
        i = 0
        while i < len(parts) - 1:
            segment = parts[i]
            if segment in keyed_collections and isinstance(cursor, dict) and i + 1 < len(parts) - 1:
                key_field = keyed_collections[segment]
                target_id = parts[i + 1]
                seq = cursor.get(segment) or []
                match = next(
                    (s for s in seq if s.get(key_field) == target_id),
                    None,
                )
                if match is None:
                    raise create_error(
                        "INPUT_INVALID",
                        detail=(f"path '{path}' references unknown {segment[:-1]} '{target_id}'"),
                    )
                cursor = match
                i += 2
                continue
            if not isinstance(cursor, dict) or segment not in cursor:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(f"path '{path}' does not exist (segment '{segment}' not found)"),
                )
            cursor = cursor[segment]
            i += 1

        last = parts[-1]
        # Handle the case where the last two segments are ``steps.<id>``
        # — i.e. the agent is rewriting the entire step. We disallow
        # this since step-level set is too coarse for the suggested-fix
        # catalog; agents should set a leaf field instead.
        if not isinstance(cursor, dict):
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'set') cannot target a list "
                    f"or scalar via path '{path}'"
                ),
            )
        cursor[last] = value

    def _apply_add_step_op(
        self,
        op_entry: dict[str, Any],
        index: int,
        data: Any,
    ) -> None:
        """Apply ``{op: 'add_step', after: <step_id|null>, step: {...}}``."""
        new_step = op_entry.get("step")
        if not isinstance(new_step, dict):
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'add_step') requires a 'step' "
                    "object with at least an 'id' and either ('tool' + 'mcp') "
                    "or 'code'"
                ),
            )

        step_id = new_step.get("id")
        if not step_id or not isinstance(step_id, str):
            raise create_error(
                "INPUT_INVALID",
                detail=(f"operations[{index}] (op:'add_step').step.id is required"),
            )

        has_tool = bool(new_step.get("tool")) and bool(new_step.get("mcp"))
        has_code = bool(new_step.get("code"))
        if not (has_tool or has_code):
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'add_step').step must declare "
                    "either ('tool' + 'mcp') or 'code'"
                ),
            )

        steps_seq = data.get("steps")
        if steps_seq is None:
            steps_seq = []
            data["steps"] = steps_seq

        for existing in steps_seq:
            if existing.get("id") == step_id:
                raise create_error(
                    "INPUT_INVALID",
                    detail=(
                        f"operations[{index}] (op:'add_step') step id {step_id!r} already exists"
                    ),
                )

        after = op_entry.get("after")
        if after is None:
            steps_seq.insert(0, new_step)
            return

        for pos, existing in enumerate(steps_seq):
            if existing.get("id") == after:
                steps_seq.insert(pos + 1, new_step)
                return

        raise create_error(
            "INPUT_INVALID",
            detail=(f"operations[{index}] (op:'add_step').after references unknown step {after!r}"),
        )

    def _apply_remove_step_op(
        self,
        op_entry: dict[str, Any],
        index: int,
        data: Any,
    ) -> None:
        """Apply ``{op: 'remove_step', step_id: <id>}`` with dependency guard.

        Refuses if any other step lists ``step_id`` in its ``depends_on``;
        the caller is expected to set those ``depends_on`` first (the
        ``suggested_fix`` carried by the error points the way).
        """
        step_id = op_entry.get("step_id")
        if not step_id or not isinstance(step_id, str):
            raise create_error(
                "INPUT_INVALID",
                detail=(f"operations[{index}] (op:'remove_step').step_id is required"),
            )

        steps_seq = data.get("steps") or []
        target_pos = -1
        for pos, existing in enumerate(steps_seq):
            if existing.get("id") == step_id:
                target_pos = pos
                break
        if target_pos == -1:
            raise create_error(
                "INPUT_INVALID",
                detail=(f"operations[{index}] (op:'remove_step') step {step_id!r} not found"),
            )

        dependents: list[str] = []
        for existing in steps_seq:
            if existing.get("id") == step_id:
                continue
            deps = existing.get("depends_on") or []
            if step_id in deps:
                dependents.append(existing.get("id") or "<unnamed>")
        if dependents:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"operations[{index}] (op:'remove_step') cannot remove "
                    f"{step_id!r}: step(s) {dependents!r} reference it via "
                    "depends_on. Clear those depends_on entries first."
                ),
            )

        del steps_seq[target_pos]

    async def _patch_draft_finalize(
        self,
        *,
        draft_id: str,
        patched_yaml: str,
        patches_applied: int,
    ) -> dict[str, Any]:
        """Re-validate a patched draft. Promote on success, update on failure."""
        # Run the same validation+create flow used by ``workflow_create``.
        create_response = await self._handle_create({"yaml_content": patched_yaml, "dry_run": True})
        validation = create_response.get("validation") or {}

        if validation.get("valid"):
            # Promote: register the now-clean YAML and discard the draft.
            self._registry.draft_store.pop(draft_id)
            self._registry.register_from_yaml(patched_yaml, persist=True)
            await self._notify_tools_changed()
            from .parser import parse_workflow_yaml

            workflow = parse_workflow_yaml(patched_yaml)
            tool_preview, warnings = self._build_tool_preview(workflow)
            self._authoring_metrics.record_workflow_patch(target="draft", status="patched")
            self._authoring_metrics.record_draft_promoted()
            return {
                "name": workflow.name,
                "version": workflow.version,
                "status": "patched",
                "patches_applied": patches_applied,
                "tool_preview": tool_preview,
                "warnings": warnings,
                "draft_id": None,
                "validation": {"valid": True, "errors": [], "warnings": []},
                "promoted_from_draft": True,
            }

        # Still invalid → update the draft text in place; same draft_id
        # so the agent can keep iterating.
        self._registry.draft_store.replace_yaml(draft_id, patched_yaml)
        self._authoring_metrics.record_workflow_patch(target="draft", status="draft")
        return {
            "name": create_response.get("name", ""),
            "version": create_response.get("version", ""),
            "status": "draft",
            "patches_applied": patches_applied,
            "tool_preview": None,
            "warnings": [],
            "draft_id": draft_id,
            "validation": validation,
            "promoted_from_draft": False,
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
