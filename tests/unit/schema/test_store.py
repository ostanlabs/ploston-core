"""F-088 · T-889 · SS-01..SS-06 -- ToolOutputSchemaStore behaviours."""

import pytest

from ploston_core.schema import (
    InMemorySchemaBackend,
    ResponsePatternExtractor,
    ToolOutputSchemaStore,
)


@pytest.fixture
def store() -> ToolOutputSchemaStore:
    return ToolOutputSchemaStore(
        backend=InMemorySchemaBackend(),
        extractor=ResponsePatternExtractor(),
    )


@pytest.mark.asyncio
async def test_observe_records_structural_schema_only(store: ToolOutputSchemaStore):
    # SS-01: first observation creates a new schema with inferred properties.
    await store.observe(
        tool_name="get_repo",
        server_name="github",
        output={"name": "ploston", "stars": 42, "archived": False},
    )

    schema = store.get("github", "get_repo")
    assert schema is not None
    assert schema.observation_count == 1
    types = schema.success_schema.types_observed
    assert types == {"object"}
    assert set(schema.success_schema.properties) == {"name", "stars", "archived"}
    # No raw values should leak into the schema.
    payload = schema.to_dict()
    assert "ploston" not in str(payload)
    assert "42" not in str(payload["success_schema"]["properties"])


@pytest.mark.asyncio
async def test_merging_marks_optional_fields_via_presence(store: ToolOutputSchemaStore):
    # SS-02: repeated observations merge; fields missing in some calls become optional.
    await store.observe("get_user", "github", {"id": 1, "email": "a@x"})
    await store.observe("get_user", "github", {"id": 2})
    await store.observe("get_user", "github", {"id": 3})

    schema = store.get("github", "get_user")
    assert schema is not None
    assert schema.observation_count == 3
    props = schema.success_schema.properties
    assert props["id"].is_required is True
    assert props["email"].is_required is False
    assert 0.0 < props["email"].presence_ratio < 1.0


@pytest.mark.asyncio
async def test_structural_change_bumps_schema_version(store: ToolOutputSchemaStore):
    # SS-03: adding a brand-new key on a subsequent observation bumps schema_version.
    await store.observe("search", "github", {"total": 1})
    schema_v1 = store.get("github", "search")
    assert schema_v1 is not None
    assert schema_v1.schema_version == 1

    await store.observe("search", "github", {"total": 2, "incomplete_results": False})
    schema_v2 = store.get("github", "search")
    assert schema_v2 is not None
    assert schema_v2.schema_version == 2


@pytest.mark.asyncio
async def test_oversize_observations_are_dropped(store: ToolOutputSchemaStore):
    # SS-04: outputs over MAX_OBSERVATION_SIZE are silently skipped.
    giant = {"data": "x" * (store.MAX_OBSERVATION_SIZE + 1)}
    await store.observe("dump", "fs", giant)
    assert store.get("fs", "dump") is None


@pytest.mark.asyncio
async def test_python_exec_is_excluded(store: ToolOutputSchemaStore):
    # SS-05: EXCLUDED_TOOLS never gets observed.
    await store.observe("python_exec", "system", {"result": 42})
    assert store.get("system", "python_exec") is None


@pytest.mark.asyncio
async def test_confidence_grows_with_consistent_observations(
    store: ToolOutputSchemaStore,
):
    # SS-06: confidence climbs as observation_count approaches threshold on
    # a consistently-shaped schema.
    for _ in range(5):
        await store.observe("uniform", "svc", {"a": 1})
    mid = store.get("svc", "uniform")
    assert mid is not None
    mid_conf = mid.confidence
    assert 0.0 < mid_conf <= 1.0

    for _ in range(10):
        await store.observe("uniform", "svc", {"a": 1})
    high = store.get("svc", "uniform")
    assert high is not None
    assert high.confidence >= mid_conf


@pytest.mark.asyncio
async def test_persisted_state_rehydrates_via_initialize():
    # SS-07: initialize() loads schemas previously persisted by the backend.
    backend = InMemorySchemaBackend()
    first = ToolOutputSchemaStore(backend=backend)
    await first.observe("ping", "svc", {"ok": True})
    assert first.get("svc", "ping") is not None

    second = ToolOutputSchemaStore(backend=backend)
    await second.initialize()
    rehydrated = second.get("svc", "ping")
    assert rehydrated is not None
    assert "ok" in rehydrated.success_schema.properties


@pytest.mark.asyncio
async def test_clear_scopes_single_tool_vs_whole_server(
    store: ToolOutputSchemaStore,
):
    # SS-08: clear() supports per-tool, per-server, and full wipes.
    await store.observe("a", "svc1", {"x": 1})
    await store.observe("b", "svc1", {"y": 2})
    await store.observe("c", "svc2", {"z": 3})

    await store.clear(server_name="svc1", tool_name="a")
    assert store.get("svc1", "a") is None
    assert store.get("svc1", "b") is not None

    await store.clear(server_name="svc1")
    assert store.get("svc1", "b") is None
    assert store.get("svc2", "c") is not None

    await store.clear()
    assert store.get("svc2", "c") is None
