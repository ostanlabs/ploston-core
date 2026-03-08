"""workflow_schema tool handler - get workflow YAML schema documentation.

Generates the schema dynamically from the actual dataclass definitions
used by the parser, ensuring it's always in sync (single source of truth).
"""

from typing import Any

from ploston_core.workflow.schema_generator import generate_workflow_schema


async def handle_workflow_schema(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Handle workflow_schema tool call.

    Args:
        arguments: Tool arguments with optional 'section'

    Returns:
        Workflow YAML schema documentation
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
        result: dict[str, Any] = {
            "section": section,
            "schema": props[section],
        }
        # Include template syntax info if asking about steps
        if section == "steps" and "template_syntax" in schema:
            result["template_syntax"] = schema["template_syntax"]
        return result

    return {
        "sections": list(schema.get("properties", {}).keys()),
        "schema": schema,
    }


# Tool schema for MCP exposure
WORKFLOW_SCHEMA_TOOL_SCHEMA = {
    "name": "ploston:workflow_schema",
    "description": (
        "Get the workflow YAML schema documentation. "
        "Returns the complete structure for authoring workflow YAML files, "
        "including all fields, types, defaults, accepted syntax variants, "
        "and a concrete example. Optionally filter by section."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": ("Optional section to get schema for. Omit for full schema."),
                "examples": [
                    "inputs",
                    "steps",
                    "outputs",
                    "defaults",
                    "packages",
                ],
            }
        },
    },
}
