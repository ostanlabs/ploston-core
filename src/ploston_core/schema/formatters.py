"""JSON Schema formatters for learned output schemas (F-088 · T-890).

Converts ``InferredJsonSchema`` instances into JSON-Schema-shaped
dictionaries suitable for embedding in MCP responses. Adds F-088-specific
metadata (``x-confidence``, ``x-observation_count``, ``x-schema_source``)
and marks optional keys with ``x-optional`` in addition to the standard
``required`` array. Never emits actual data values.
"""

from __future__ import annotations

from typing import Any

from .types import InferredJsonSchema, SuggestedOutputSchema


def format_inferred_schema(
    schema: SuggestedOutputSchema,
    *,
    include_error_schema: bool = False,
) -> dict[str, Any]:
    """Render a learned ``SuggestedOutputSchema`` as a JSON-Schema dict.

    The result is safe to embed in ``outputSchema`` on MCP tool descriptors
    or in ``workflow_tool_schema`` responses.
    """
    body = _format(schema.success_schema)
    body["x-confidence"] = schema.confidence
    body["x-observation_count"] = schema.observation_count
    body["x-schema_source"] = "learned"
    body["x-schema_version"] = schema.schema_version
    if include_error_schema and schema.error_schema is not None:
        body["x-error_schema"] = _format(schema.error_schema)
    return body


def _format(schema: InferredJsonSchema) -> dict[str, Any]:
    types = sorted(schema.types_observed)
    out: dict[str, Any] = {}
    if len(types) == 1:
        out["type"] = types[0]
    elif types:
        out["type"] = types

    if schema.properties:
        required: list[str] = []
        optional: list[str] = []
        properties: dict[str, Any] = {}
        for name in sorted(schema.properties):
            prop = schema.properties[name]
            child = _format(prop.field_schema)
            child["x-frequency"] = prop.frequency
            child["x-presence_ratio"] = round(prop.presence_ratio, 4)
            properties[name] = child
            if prop.is_required:
                required.append(name)
            else:
                optional.append(name)
        out["properties"] = properties
        if required:
            out["required"] = required
        if optional:
            out["x-optional"] = optional

    if schema.items_schema is not None:
        out["items"] = _format(schema.items_schema)

    return out
