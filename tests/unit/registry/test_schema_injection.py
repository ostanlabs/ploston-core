"""F-088 · T-901 · SI-01..SI-07 -- learned-schema injection into tools/list.

Covers both ``ToolDefinition.to_mcp_tool`` priority logic and
``ToolRegistry.get_for_mcp_exposure`` confidence-gated injection.
"""

from unittest.mock import MagicMock

import pytest

from ploston_core.config.models import ToolsConfig
from ploston_core.registry import ToolRegistry
from ploston_core.registry.types import ToolDefinition
from ploston_core.schema import InMemorySchemaBackend, ToolOutputSchemaStore
from ploston_core.types import ToolSource, ToolStatus


def _tool(
    name: str = "get_repo",
    server: str = "github",
    *,
    output_schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="d",
        source=ToolSource.MCP,
        server_name=server,
        input_schema={"type": "object"},
        output_schema=output_schema,
        status=ToolStatus.AVAILABLE,
    )


def test_si01_to_mcp_tool_without_suggested_omits_output_schema():
    payload = _tool().to_mcp_tool()
    assert "outputSchema" not in payload


def test_si02_to_mcp_tool_injects_suggested_when_declared_absent():
    suggested = {"type": "object", "x-schema_source": "learned"}
    payload = _tool().to_mcp_tool(suggested_output_schema=suggested)
    assert payload["outputSchema"] == suggested


def test_si03_to_mcp_tool_mcp_declared_wins_over_suggested():
    declared = {"type": "object", "x-source": "declared"}
    suggested = {"type": "object", "x-source": "learned"}
    payload = _tool(output_schema=declared).to_mcp_tool(suggested_output_schema=suggested)
    assert payload["outputSchema"] == declared


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry(mcp_manager=MagicMock(), config=ToolsConfig(), logger=None)
    # Tools are stored under their bare MCP names; server_name is a separate
    # attribute. Exposure names flatten to {server}__{tool}.
    reg._tools = {
        "get_repo": _tool("get_repo", "github"),
        "post_message": _tool("post_message", "slack"),
    }
    return reg


@pytest.mark.asyncio
async def test_si04_injection_only_for_confident_tools(registry: ToolRegistry):
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    for _ in range(20):
        await store.observe("get_repo", "github", {"id": 1, "name": "x"})
    registry.set_schema_store(store, min_confidence=0.8)

    exposed = {t["name"]: t for t in registry.get_for_mcp_exposure()}
    assert "outputSchema" in exposed["get_repo"]
    assert exposed["get_repo"]["outputSchema"]["x-schema_source"] == "learned"
    assert "outputSchema" not in exposed["post_message"]


@pytest.mark.asyncio
async def test_si05_below_threshold_tools_are_skipped(registry: ToolRegistry):
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("get_repo", "github", {"id": 1})
    entry = store.get("github", "get_repo")
    assert entry is not None
    assert entry.confidence < 0.8

    registry.set_schema_store(store, min_confidence=0.8)
    exposed = {t["name"]: t for t in registry.get_for_mcp_exposure()}
    assert "outputSchema" not in exposed["get_repo"]


def test_si06_no_schema_store_preserves_previous_behaviour(registry: ToolRegistry):
    exposed = registry.get_for_mcp_exposure()
    for payload in exposed:
        assert "outputSchema" not in payload


@pytest.mark.asyncio
async def test_si07_injection_honours_configured_threshold(registry: ToolRegistry):
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    for _ in range(5):
        await store.observe("get_repo", "github", {"id": 1, "name": "x"})
    entry = store.get("github", "get_repo")
    assert entry is not None
    observed = entry.confidence

    registry.set_schema_store(store, min_confidence=max(0.0, observed - 0.1))
    exposed = {t["name"]: t for t in registry.get_for_mcp_exposure()}
    assert "outputSchema" in exposed["get_repo"]

    registry.set_schema_store(store, min_confidence=min(1.0, observed + 0.05))
    exposed = {t["name"]: t for t in registry.get_for_mcp_exposure()}
    assert "outputSchema" not in exposed["get_repo"]


@pytest.mark.asyncio
async def test_si08_mcp_declared_wins_even_when_learned_is_confident(
    registry: ToolRegistry,
):
    declared = {"type": "object", "x-source": "declared"}
    registry._tools["get_repo"] = _tool("get_repo", "github", output_schema=declared)
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    for _ in range(20):
        await store.observe("get_repo", "github", {"id": 1, "name": "x"})
    registry.set_schema_store(store, min_confidence=0.8)

    exposed = {t["name"]: t for t in registry.get_for_mcp_exposure()}
    assert exposed["get_repo"]["outputSchema"] == declared


@pytest.mark.asyncio
async def test_si09_get_suggested_schema_default_uses_configured_floor(
    registry: ToolRegistry,
):
    """Inspector Schema Visibility -- default ``min_confidence=None`` keeps the
    configured floor so agent-facing callers (workflow paths, internal lookups)
    behave exactly as before."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("get_repo", "github", {"id": 1})
    entry = store.get("github", "get_repo")
    assert entry is not None and entry.confidence < 0.8

    registry.set_schema_store(store, min_confidence=0.8)

    # Default (no override) -> configured 0.8 floor -> nothing surfaced.
    assert registry.get_suggested_schema("get_repo") is None


@pytest.mark.asyncio
async def test_si10_get_suggested_schema_min_confidence_zero_surfaces_low_confidence(
    registry: ToolRegistry,
):
    """Inspector REST passes ``min_confidence=0.0`` so operators see learned
    schemas long before they reach the agent-facing 0.8 threshold."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("get_repo", "github", {"id": 1})
    entry = store.get("github", "get_repo")
    assert entry is not None and entry.confidence < 0.8

    registry.set_schema_store(store, min_confidence=0.8)

    surfaced = registry.get_suggested_schema("get_repo", min_confidence=0.0)
    assert surfaced is not None
    assert surfaced.get("x-schema_source") == "learned"


@pytest.mark.asyncio
async def test_si11_min_confidence_override_does_not_bypass_declared_schema(
    registry: ToolRegistry,
):
    """Even with ``min_confidence=0.0`` the declared-schema-wins rule holds:
    a tool that already has an MCP-declared output schema must never receive
    a learned overlay."""
    declared = {"type": "object", "x-source": "declared"}
    registry._tools["get_repo"] = _tool("get_repo", "github", output_schema=declared)
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    for _ in range(20):
        await store.observe("get_repo", "github", {"id": 1, "name": "x"})
    registry.set_schema_store(store, min_confidence=0.8)

    assert registry.get_suggested_schema("get_repo", min_confidence=0.0) is None


# ---------------------------------------------------------------------------
# Inspector Schema Visibility (Phase 2) — runner-hosted / canonical-name
# resolution. The schema store keys observations by ``(server, bare_tool)``
# derived from the canonical name in ``ToolInvoker._observe_output_safe``.
# These tests pin the registry's read paths so the inspector stops missing
# learned schemas for runner-hosted tools.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_si12_canonical_name_resolves_when_tool_not_in_registry(
    registry: ToolRegistry,
):
    """Runner-hosted tools never enter ``ToolRegistry``. The inspector REST
    layer asks for them by canonical ``<mcp>__<tool>`` name; the lookup must
    decode that into the bare ``(server, tool)`` key the store uses."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("search_code", "github", {"status": "ok", "result": []})
    registry.set_schema_store(store, min_confidence=0.8)

    surfaced = registry.get_suggested_schema("github__search_code", min_confidence=0.0)
    assert surfaced is not None
    assert surfaced.get("x-schema_source") == "learned"


@pytest.mark.asyncio
async def test_si13_explicit_server_name_hint_resolves_bare_name(
    registry: ToolRegistry,
):
    """Callers that already know the runner's MCP server can pass it
    explicitly. The lookup must accept the hint without requiring the
    tool to be present in ``ToolRegistry``."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("issues_list", "github", {"status": "ok"})
    registry.set_schema_store(store, min_confidence=0.8)

    surfaced = registry.get_suggested_schema(
        "issues_list", server_name="github", min_confidence=0.0
    )
    assert surfaced is not None
    assert surfaced.get("x-schema_source") == "learned"


@pytest.mark.asyncio
async def test_si14_canonical_name_with_redundant_server_hint_works(
    registry: ToolRegistry,
):
    """Defensive: when caller has both a canonical name and a server hint,
    the lookup must not double-prefix the bare tool name."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("get_pr", "github", {"status": "ok"})
    registry.set_schema_store(store, min_confidence=0.8)

    surfaced = registry.get_suggested_schema(
        "github__get_pr", server_name="github", min_confidence=0.0
    )
    assert surfaced is not None


@pytest.mark.asyncio
async def test_si15_no_false_positive_for_unrelated_tool(registry: ToolRegistry):
    """A learned schema for ``github__search_code`` must not surface when
    asked about ``github__list_repos``. The lookup is exact at the
    ``(server, bare)`` level."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("search_code", "github", {"status": "ok"})
    registry.set_schema_store(store, min_confidence=0.8)

    assert registry.get_suggested_schema("github__list_repos", min_confidence=0.0) is None


@pytest.mark.asyncio
async def test_si16_in_registry_tool_with_bare_name_still_works(
    registry: ToolRegistry,
):
    """Regression guard for the original CP-direct path: a registry tool
    stored under its bare name with an explicit ``server_name`` continues
    to resolve via the public API."""
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    await store.observe("get_repo", "github", {"id": 1, "name": "x"})
    registry.set_schema_store(store, min_confidence=0.8)

    surfaced = registry.get_suggested_schema("get_repo", min_confidence=0.0)
    assert surfaced is not None
