"""F-088 · T-892 · SF-01..SF-04 -- JSON Schema formatter output invariants."""

from datetime import datetime

from ploston_core.schema import format_inferred_schema
from ploston_core.schema.types import (
    InferredJsonSchema,
    InferredPropertySchema,
    SuggestedOutputSchema,
)


def _build_schema() -> SuggestedOutputSchema:
    success = InferredJsonSchema(
        types_observed={"object"},
        observation_count=5,
        properties={
            "id": InferredPropertySchema(
                field_schema=InferredJsonSchema(types_observed={"integer"}, observation_count=5),
                frequency=5,
                total_observations=5,
            ),
            "tags": InferredPropertySchema(
                field_schema=InferredJsonSchema(
                    types_observed={"array"},
                    items_schema=InferredJsonSchema(types_observed={"string"}),
                ),
                frequency=3,
                total_observations=5,
            ),
        },
    )
    return SuggestedOutputSchema(
        tool_name="list_tags",
        server_name="git",
        success_schema=success,
        observation_count=5,
        confidence=0.7,
        schema_version=2,
        first_observed=datetime(2026, 4, 1),
        last_observed=datetime(2026, 4, 24),
    )


def test_formatter_emits_required_and_optional_keys():
    # SF-01: keys with frequency == total_observations go into `required`;
    # others land in `x-optional`.
    payload = format_inferred_schema(_build_schema())

    assert payload["type"] == "object"
    assert payload["required"] == ["id"]
    assert payload["x-optional"] == ["tags"]


def test_formatter_annotates_confidence_and_observation_count():
    # SF-02: F-088-specific metadata must be present at the top level.
    payload = format_inferred_schema(_build_schema())

    assert payload["x-confidence"] == 0.7
    assert payload["x-observation_count"] == 5
    assert payload["x-schema_source"] == "learned"
    assert payload["x-schema_version"] == 2


def test_formatter_annotates_per_property_frequency_and_presence():
    # SF-03: every property carries per-field frequency + presence_ratio.
    payload = format_inferred_schema(_build_schema())

    id_prop = payload["properties"]["id"]
    tags_prop = payload["properties"]["tags"]
    assert id_prop["x-frequency"] == 5
    assert id_prop["x-presence_ratio"] == 1.0
    assert tags_prop["x-frequency"] == 3
    assert tags_prop["x-presence_ratio"] == 0.6


def test_formatter_emits_items_schema_for_arrays():
    # SF-04: nested array item schema is rendered as `items`.
    payload = format_inferred_schema(_build_schema())
    tags_prop = payload["properties"]["tags"]
    assert tags_prop["type"] == "array"
    assert tags_prop["items"]["type"] == "string"


def test_formatter_handles_union_types_as_list():
    # SF-05: multiple observed types become a JSON Schema list, not a set.
    schema = SuggestedOutputSchema(
        tool_name="t",
        server_name="s",
        success_schema=InferredJsonSchema(types_observed={"string", "null"}, observation_count=2),
        observation_count=2,
    )
    payload = format_inferred_schema(schema)
    assert payload["type"] == ["null", "string"]


def test_formatter_omits_x_optional_when_all_required():
    # SF-06: no `x-optional` key when every property is always present.
    schema = SuggestedOutputSchema(
        tool_name="t",
        server_name="s",
        success_schema=InferredJsonSchema(
            types_observed={"object"},
            observation_count=2,
            properties={
                "a": InferredPropertySchema(
                    field_schema=InferredJsonSchema(types_observed={"string"}),
                    frequency=2,
                    total_observations=2,
                )
            },
        ),
        observation_count=2,
    )
    payload = format_inferred_schema(schema)
    assert payload["required"] == ["a"]
    assert "x-optional" not in payload
