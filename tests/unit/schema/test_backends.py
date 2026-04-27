"""F-088 · T-889 · SB-01..SB-03 -- backend persistence round-trips."""

from pathlib import Path

import pytest

from ploston_core.schema import (
    FileSchemaBackend,
    InMemorySchemaBackend,
    ResponsePatternExtractor,
    ToolOutputSchemaStore,
)
from ploston_core.schema.extractor import ExtractionPattern, PatternType
from ploston_core.schema.types import InferredJsonSchema, SuggestedOutputSchema


@pytest.mark.asyncio
async def test_file_backend_persists_schemas_across_instances(tmp_path: Path):
    # SB-01: schema survives process boundary via JSON file persistence.
    backend = FileSchemaBackend(data_dir=tmp_path)
    schema = SuggestedOutputSchema(
        tool_name="ping",
        server_name="svc",
        success_schema=InferredJsonSchema(types_observed={"object"}),
    )
    await backend.save("svc__ping", schema)

    reopened = FileSchemaBackend(data_dir=tmp_path)
    entries = await reopened.load_all()

    assert "svc__ping" in entries
    loaded_schema, _ = entries["svc__ping"]
    assert loaded_schema.tool_name == "ping"
    assert loaded_schema.server_name == "svc"


@pytest.mark.asyncio
async def test_file_backend_round_trips_extraction_patterns(tmp_path: Path):
    # SB-02: Layer-1 patterns are stored alongside the schema and reloaded.
    backend = FileSchemaBackend(data_dir=tmp_path)
    schema = SuggestedOutputSchema(
        tool_name="ping",
        server_name="svc",
        success_schema=InferredJsonSchema(types_observed={"object"}),
    )
    pattern = ExtractionPattern(
        tool_key="svc__ping",
        pattern_type=PatternType.PREFIX_JSON,
        prefix_length=12,
        prefix_sample="Result: ",
        observation_count=3,
        match_count=3,
    )
    await backend.save("svc__ping", schema, pattern)

    entries = await FileSchemaBackend(data_dir=tmp_path).load_all()
    _, loaded_pattern = entries["svc__ping"]
    assert loaded_pattern is not None
    assert loaded_pattern.pattern_type == PatternType.PREFIX_JSON
    assert loaded_pattern.prefix_length == 12
    assert loaded_pattern.consistency == 1.0


@pytest.mark.asyncio
async def test_store_end_to_end_with_file_backend(tmp_path: Path):
    # SB-03: ToolOutputSchemaStore + FileSchemaBackend rehydrate on reopen.
    backend = FileSchemaBackend(data_dir=tmp_path)
    store = ToolOutputSchemaStore(backend=backend, extractor=ResponsePatternExtractor())
    await store.observe("call", "svc", {"id": 1, "tags": ["a", "b"]})

    reopened_store = ToolOutputSchemaStore(
        backend=FileSchemaBackend(data_dir=tmp_path),
        extractor=ResponsePatternExtractor(),
    )
    await reopened_store.initialize()

    schema = reopened_store.get("svc", "call")
    assert schema is not None
    assert "id" in schema.success_schema.properties
    assert "tags" in schema.success_schema.properties


@pytest.mark.asyncio
async def test_inmemory_backend_delete_and_clear(tmp_path: Path):
    # SB-04: in-memory backend honours delete/clear (used by tests).
    backend = InMemorySchemaBackend()
    schema = SuggestedOutputSchema(
        tool_name="t",
        server_name="s",
        success_schema=InferredJsonSchema(types_observed={"null"}),
    )
    await backend.save("s__t", schema)
    assert "s__t" in await backend.load_all()
    await backend.delete("s__t")
    assert "s__t" not in await backend.load_all()
    await backend.save("s__t", schema)
    await backend.clear_all()
    assert await backend.load_all() == {}
