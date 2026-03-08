"""Workflow CRUD tools for MCP exposure.

Provides flat-named workflow management tools (workflow_schema, workflow_list,
workflow_get, workflow_create, workflow_update, workflow_delete, workflow_validate)
that delegate to the WorkflowRegistry.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from ploston_core.errors import create_error

from .schema_generator import generate_workflow_schema

# ─────────────────────────────────────────────────────────────────
# Tool Names (static set for routing disambiguation)
# ─────────────────────────────────────────────────────────────────

WORKFLOW_CRUD_TOOL_NAMES = frozenset(
    {
        "workflow_schema",
        "workflow_list",
        "workflow_get",
        "workflow_create",
        "workflow_update",
        "workflow_delete",
        "workflow_validate",
    }
)

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
    "description": "List all registered workflows with metadata.",
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
    "description": "Get a workflow's YAML definition and metadata by name.",
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
        "Register a new workflow from YAML content. "
        "Use workflow_schema first to learn the YAML format and see a concrete example."
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
        "Update an existing workflow with new YAML content. "
        "Use workflow_get to retrieve the current definition and workflow_schema for the YAML format reference."
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

ALL_WORKFLOW_CRUD_TOOLS = [
    WORKFLOW_SCHEMA_TOOL,
    WORKFLOW_LIST_TOOL,
    WORKFLOW_GET_TOOL,
    WORKFLOW_CREATE_TOOL,
    WORKFLOW_UPDATE_TOOL,
    WORKFLOW_DELETE_TOOL,
    WORKFLOW_VALIDATE_TOOL,
]


# ─────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────


class WorkflowToolsProvider:
    """Provides workflow CRUD tools for MCP exposure.

    Sits alongside WorkflowRegistry, delegates all operations to it.
    Exposed via MCPFrontend in running mode.
    """

    def __init__(
        self,
        workflow_registry: Any,
        tool_registry: Any | None = None,
        runner_registry: Any | None = None,
        on_tools_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        from .registry import WorkflowRegistry

        self._registry: WorkflowRegistry = workflow_registry
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
        self._on_tools_changed = on_tools_changed
        self._handlers: dict[str, Callable[..., Any]] = {
            "workflow_schema": self._handle_schema,
            "workflow_list": self._handle_list,
            "workflow_get": self._handle_get,
            "workflow_create": self._handle_create,
            "workflow_update": self._handle_update,
            "workflow_delete": self._handle_delete,
            "workflow_validate": self._handle_validate,
        }

    @staticmethod
    def is_crud_tool(name: str) -> bool:
        """Check if a tool name is a workflow CRUD tool (not an execution tool)."""
        return name in WORKFLOW_CRUD_TOOL_NAMES

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas for all CRUD tools."""
        return list(ALL_WORKFLOW_CRUD_TOOLS)

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
                runners = self._runner_registry.list_runners()
                for runner in runners:
                    runner_name = runner.name if hasattr(runner, "name") else str(runner)
                    available = getattr(runner, "available_tools", None) or []
                    # available_tools are prefixed: "mcp__tool"
                    by_server: dict[str, list[str]] = {}
                    for prefixed_name in available:
                        parts = prefixed_name.split("__", 1)
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
        return {"name": workflow.name, "version": workflow.version, "status": "created"}

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

        return {"name": workflow.name, "version": workflow.version, "status": "updated"}

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
        return {
            "valid": result.valid,
            "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            "warnings": [{"path": w.path, "message": w.message} for w in result.warnings],
        }


def _to_json(data: Any) -> str:
    """Serialize data to JSON string."""
    import json

    return json.dumps(data, default=str)
