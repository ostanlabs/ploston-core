"""F-088 · T-889 · ST-01..ST-05 -- serialisation invariants for schema dataclasses."""

from datetime import datetime

from ploston_core.schema.types import (
    InferredJsonSchema,
    InferredPropertySchema,
    SuggestedOutputSchema,
)


def _build_sample_inferred() -> InferredJsonSchema:
    nested = InferredJsonSchema(types_observed={"integer"}, observation_count=4)
    return InferredJsonSchema(
        types_observed={"object"},
        observation_count=5,
        properties={
            "id": InferredPropertySchema(field_schema=nested, frequency=5, total_observations=5),
            "maybe": InferredPropertySchema(
                field_schema=InferredJsonSchema(
                    types_observed={"string", "null"}, observation_count=3
                ),
                frequency=3,
                total_observations=5,
            ),
        },
    )


def test_inferred_roundtrip_preserves_nested_structure_and_counts():
    # ST-01: to_dict / from_dict round-trip for InferredJsonSchema.
    schema = _build_sample_inferred()

    roundtripped = InferredJsonSchema.from_dict(schema.to_dict())

    assert roundtripped.types_observed == {"object"}
    assert roundtripped.observation_count == 5
    assert set(roundtripped.properties) == {"id", "maybe"}
    assert roundtripped.properties["id"].frequency == 5
    assert roundtripped.properties["id"].field_schema.types_observed == {"integer"}
    assert roundtripped.properties["maybe"].field_schema.types_observed == {
        "null",
        "string",
    }


def test_property_presence_required_and_ratio_edge_cases():
    # ST-02: required vs optional semantics + presence_ratio.
    required_prop = InferredPropertySchema(
        field_schema=InferredJsonSchema(types_observed={"string"}),
        frequency=4,
        total_observations=4,
    )
    optional_prop = InferredPropertySchema(
        field_schema=InferredJsonSchema(types_observed={"string"}),
        frequency=2,
        total_observations=4,
    )
    never_seen = InferredPropertySchema(
        field_schema=InferredJsonSchema(types_observed={"null"}),
        frequency=0,
        total_observations=3,
    )

    assert required_prop.is_required is True
    assert required_prop.presence_ratio == 1.0
    assert optional_prop.is_required is False
    assert optional_prop.presence_ratio == 0.5
    # frequency==0 is never required; presence_ratio is 0.0.
    assert never_seen.is_required is False
    assert never_seen.presence_ratio == 0.0
    # Guard against div-by-zero when total_observations=0.
    zero = InferredPropertySchema(
        field_schema=InferredJsonSchema(types_observed={"string"}),
        frequency=0,
        total_observations=0,
    )
    assert zero.presence_ratio == 0.0


def test_suggested_schema_timestamps_and_variants_roundtrip():
    # ST-03: SuggestedOutputSchema keeps timestamps + optional variant_schemas.
    now = datetime(2026, 4, 24, 12, 0, 0)
    later = datetime(2026, 4, 24, 13, 30, 0)
    schema = SuggestedOutputSchema(
        tool_name="actions_list",
        server_name="github",
        success_schema=_build_sample_inferred(),
        error_schema=InferredJsonSchema(types_observed={"string"}),
        observation_count=7,
        error_count=1,
        first_observed=now,
        last_observed=later,
        schema_version=2,
        confidence=0.42,
        variant_schemas={"empty": InferredJsonSchema(types_observed={"array"})},
    )

    roundtripped = SuggestedOutputSchema.from_dict(schema.to_dict())

    assert roundtripped.tool_name == "actions_list"
    assert roundtripped.server_name == "github"
    assert roundtripped.first_observed == now
    assert roundtripped.last_observed == later
    assert roundtripped.schema_version == 2
    assert roundtripped.confidence == 0.42
    assert roundtripped.error_schema is not None
    assert roundtripped.error_count == 1
    assert roundtripped.variant_schemas is not None
    assert "empty" in roundtripped.variant_schemas


def test_types_observed_is_always_json_safe_sorted_list():
    # ST-04: sets become sorted lists in serialised form (safe for JSON).
    schema = InferredJsonSchema(types_observed={"string", "integer", "null"})
    payload = schema.to_dict()
    assert payload["types_observed"] == ["integer", "null", "string"]


def test_items_schema_roundtrips_when_present_and_absent():
    # ST-05: optional items_schema.
    with_items = InferredJsonSchema(
        types_observed={"array"},
        items_schema=InferredJsonSchema(types_observed={"integer"}),
    )
    without_items = InferredJsonSchema(types_observed={"object"})

    assert InferredJsonSchema.from_dict(with_items.to_dict()).items_schema is not None
    assert InferredJsonSchema.from_dict(without_items.to_dict()).items_schema is None
