"""Workflow management tools for MCP exposure.

Provides flat-named workflow management tools (workflow_schema, workflow_list,
workflow_get, workflow_create, workflow_update, workflow_delete, workflow_validate,
workflow_tool_schema, workflow_run) that delegate to WorkflowRegistry / WorkflowEngine.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ploston_core.errors import create_error

from .schema_generator import generate_workflow_schema

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
        "workflow_get",
        "workflow_get_definition",
        "workflow_create",
        "workflow_update",
        "workflow_delete",
        "workflow_validate",
        "workflow_tool_schema",
        "workflow_run",
    }
)

# Backward-compat alias (deprecated)
WORKFLOW_CRUD_TOOL_NAMES = WORKFLOW_MGMT_TOOL_NAMES

# ─────────────────────────────────────────────────────────────────
# MCP Tool Schemas
# ─────────────────────────────────────────────────────────────────

WORKFLOW_SCHEMA_TOOL = {
    "name": "workflow_schema",
    "description": (
        "Get the workflow YAML schema documentation and list of available tools. "
        "Returns the complete structure for authoring workflow YAML files, "
        "including all fields, types, defaults, accepted syntax variants, "
        "a concrete example, and a live 'available_tools' list showing every "
        "tool grouped by MCP server and runner. Use this to discover which "
        "tools can be referenced in workflow tool steps."
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
}

WORKFLOW_CREATE_TOOL = {
    "name": "workflow_create",
    "description": (
        "Publish a workflow as an MCP tool. Once registered, the workflow appears in "
        "tools/list under its bare name — its `description` field becomes what agents "
        "see when selecting tools, and each input `description` becomes a parameter doc. "
        "Use workflow_schema first to learn the YAML format. "
        "Returns a preview of how the tool will appear in tools/list."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["yaml_content"],
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": "Workflow definition in YAML format. Call workflow_schema to see the expected structure.",
            }
        },
    },
}

WORKFLOW_UPDATE_TOOL = {
    "name": "workflow_update",
    "description": (
        "Update a published workflow tool. The workflow's `description` and input "
        "`description` fields are the public tool interface seen by other agents — "
        "update them as you would update tool documentation. "
        "Use workflow_get to retrieve the current definition. "
        "Returns a preview of how the updated tool will appear in tools/list."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["name", "yaml_content"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the workflow to update.",
            },
            "yaml_content": {
                "type": "string",
                "description": "Updated workflow definition in YAML format. Call workflow_schema to see the expected structure.",
            },
        },
    },
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
}

WORKFLOW_TOOL_SCHEMA_TOOL = {
    "name": "workflow_tool_schema",
    "description": (
        "Get the full parameter schema for a specific tool by its MCP server "
        "name and tool name. Returns the tool description and inputSchema so "
        "you can correctly fill the params block when authoring a workflow. "
        "Use workflow_schema first to discover available mcp server names and "
        "tool names, then call this to get the schema for the specific tool "
        "you want to use."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["mcp", "tool"],
        "properties": {
            "mcp": {
                "type": "string",
                "description": (
                    "MCP server name hosting the tool (e.g. github, filesystem, system). "
                    "Must match a value from workflow_schema available_tools."
                ),
            },
            "tool": {
                "type": "string",
                "description": (
                    "Bare tool name as registered on the MCP server "
                    "(e.g. actions_list, read_file). Do not include runner or mcp prefix."
                ),
            },
        },
    },
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
}

ALL_WORKFLOW_MGMT_TOOLS: list[dict[str, Any]] = [
    WORKFLOW_SCHEMA_TOOL,
    WORKFLOW_LIST_TOOL,
    WORKFLOW_GET_TOOL,
    WORKFLOW_GET_DEFINITION_TOOL,
    WORKFLOW_CREATE_TOOL,
    WORKFLOW_UPDATE_TOOL,
    WORKFLOW_DELETE_TOOL,
    WORKFLOW_VALIDATE_TOOL,
    WORKFLOW_TOOL_SCHEMA_TOOL,
    WORKFLOW_RUN_TOOL,
]

# Backward-compat alias (deprecated)
ALL_WORKFLOW_CRUD_TOOLS = ALL_WORKFLOW_MGMT_TOOLS


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
        on_tools_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        from .registry import WorkflowRegistry

        self._registry: WorkflowRegistry = workflow_registry
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
        self._workflow_engine = workflow_engine
        self._on_tools_changed = on_tools_changed
        self._handlers: dict[str, Callable[..., Any]] = {
            "workflow_schema": self._handle_schema,
            "workflow_list": self._handle_list,
            "workflow_get": self._handle_get,
            "workflow_get_definition": self._handle_get_definition,
            "workflow_create": self._handle_create,
            "workflow_update": self._handle_update,
            "workflow_delete": self._handle_delete,
            "workflow_validate": self._handle_validate,
            "workflow_tool_schema": self._handle_tool_schema,
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
        """Route tool call to handler. Returns MCP-format response."""
        handler = self._handlers.get(name)
        if not handler:
            raise create_error("TOOL_NOT_AVAILABLE", tool_name=name)
        result = await handler(arguments)
        return {
            "content": [{"type": "text", "text": _to_json(result)}],
            "isError": False,
        }

    async def _notify_tools_changed(self) -> None:
        """Fire the on_tools_changed callback if registered."""
        if self._on_tools_changed:
            await self._on_tools_changed()

    # ── Handlers ──────────────────────────────────────────────────

    async def _handle_schema(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_schema tool call.

        Returns the static YAML schema plus a dynamic ``available_tools``
        list built from the live ToolRegistry and RunnerRegistry.
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

        response: dict[str, Any] = {
            "authoring_note": "Author the workflow. Document the tool.",
            "sections": list(schema.get("properties", {}).keys()),
            "schema": schema,
        }

        # Inject dynamic available_tools (T-724)
        available = self._build_available_tools()
        if available:
            response["available_tools"] = available

        return response

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
                    by_server: dict[str, list[str]] = {}
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
                            by_server.setdefault(server, []).append(tool)
                    for server_name, tool_names in sorted(by_server.items()):
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

        tool_preview = {
            "note": "Agents will see this tool in tools/list:",
            "tool_name": workflow.name,
            "tool_description": description,
            "parameters": {inp["name"]: inp["description"] for inp in inputs_preview},
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

        # register_from_yaml raises AELError(INPUT_INVALID) on validation failure
        self._registry.register_from_yaml(yaml_content, persist=True)
        await self._notify_tools_changed()

        tool_preview, warnings = self._build_tool_preview(workflow)
        return {
            "name": workflow.name,
            "version": workflow.version,
            "status": "created",
            "tool_preview": tool_preview,
            "warnings": warnings,
        }

    async def _handle_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_update tool call."""
        name = arguments.get("name")
        yaml_content = arguments.get("yaml_content")
        if not name:
            raise create_error("PARAM_INVALID", message="'name' parameter is required")
        if not yaml_content:
            raise create_error("PARAM_INVALID", message="'yaml_content' parameter is required")

        existing = self._registry.get(name)
        if not existing:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)

        from .parser import parse_workflow_yaml

        workflow = parse_workflow_yaml(yaml_content)
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
        return {
            "name": workflow.name,
            "version": workflow.version,
            "status": "updated",
            "tool_preview": tool_preview,
            "warnings": warnings,
        }

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

        # Description quality check (warnings only — never affects valid)
        desc_warnings: list[dict[str, str]] = []
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
        except Exception:
            desc_warnings = []

        return {
            "valid": result.valid,
            "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            "warnings": (
                [{"path": w.path, "message": w.message} for w in result.warnings] + desc_warnings
            ),
        }

    # S-272 T-863: shared hint surfaced by workflow_tool_schema on all branches.
    # MCP tool definitions only carry input schemas, so agents need an explicit
    # pointer to inspect the actual response shape at runtime.
    _TOOL_SCHEMA_RESPONSE_HINT = (
        "Output schemas are not available from MCP tool definitions. "
        "Use workflow_run with context.log() to inspect actual response shapes. "
        "Tool step outputs are automatically normalized — transport envelopes "
        "(status/result/content wrappers, content-block arrays) are stripped."
    )

    async def _handle_tool_schema(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle workflow_tool_schema tool call.

        Returns the full parameter schema for a specific tool, resolved using
        the same CP-first order as the unified tool resolver.
        """
        mcp = arguments.get("mcp")
        tool = arguments.get("tool")

        if not mcp:
            raise create_error("PARAM_INVALID", message="'mcp' parameter is required")
        if not tool:
            raise create_error("PARAM_INVALID", message="'tool' parameter is required")

        response_hint = self._TOOL_SCHEMA_RESPONSE_HINT

        # Step 1: CP-direct -- ToolRegistry lookup by server_name
        # Covers mcp: system (python_exec), mcp: github (CP-registered), etc.
        if self._tool_registry:
            cp_tools = self._tool_registry.list_tools(server_name=mcp)
            for tool_def in cp_tools:
                if tool_def.name == tool:
                    return {
                        "mcp_server": mcp,
                        "tool": tool,
                        "runner": None,
                        "description": tool_def.description,
                        "input_schema": tool_def.input_schema or {},
                        "source": "cp",
                        "response_hint": response_hint,
                    }

        # Step 2: Runner-hosted -- RunnerRegistry lookup
        if self._runner_registry:
            canonical = f"{mcp}__{tool}"
            for runner in self._runner_registry.list():
                for entry in runner.available_tools or []:
                    if isinstance(entry, str):
                        if entry == canonical:
                            return {
                                "mcp_server": mcp,
                                "tool": tool,
                                "runner": runner.name,
                                "description": None,
                                "input_schema": {},
                                "source": "runner",
                                "response_hint": response_hint,
                            }
                    elif isinstance(entry, dict):
                        if entry.get("name") == canonical:
                            return {
                                "mcp_server": mcp,
                                "tool": tool,
                                "runner": runner.name,
                                "description": entry.get("description"),
                                "input_schema": entry.get("inputSchema") or {},
                                "source": "runner",
                                "response_hint": response_hint,
                            }

        # Not found -- return structured error with hint
        available = self._build_available_tools()
        return {
            "found": False,
            "mcp_server": mcp,
            "tool": tool,
            "error": (
                f"Tool '{tool}' not found on MCP server '{mcp}'. "
                "Use workflow_schema to see available tools."
            ),
            "available_tools": available,
            "response_hint": response_hint,
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
