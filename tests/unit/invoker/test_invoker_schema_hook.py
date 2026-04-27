"""F-088 · T-889 · SH-01..SH-04 -- invoker schema observation hook."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.invoker import ToolInvoker
from ploston_core.schema import InMemorySchemaBackend, ToolOutputSchemaStore
from ploston_core.types import ToolSource, ToolStatus


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    tool = MagicMock()
    tool.status = ToolStatus.AVAILABLE
    registry.get_or_raise.return_value = tool
    router = MagicMock()
    router.source = ToolSource.MCP
    router.server_name = "github"
    registry.get_router.return_value = router
    return registry


@pytest.fixture
def mock_mcp_manager():
    mgr = MagicMock()
    mcp_result = MagicMock()
    mcp_result.is_error = False
    mcp_result.content = '{"name": "ploston", "stars": 42}'
    mcp_result.structured_content = {"name": "ploston", "stars": 42}
    mgr.call_tool = AsyncMock(return_value=mcp_result)
    return mgr


@pytest.fixture
def mock_sandbox_factory():
    return MagicMock()


@pytest.fixture
def schema_store() -> ToolOutputSchemaStore:
    return ToolOutputSchemaStore(backend=InMemorySchemaBackend())


@pytest.fixture
def invoker(mock_registry, mock_mcp_manager, mock_sandbox_factory, schema_store):
    inv = ToolInvoker(
        tool_registry=mock_registry,
        mcp_manager=mock_mcp_manager,
        sandbox_factory=mock_sandbox_factory,
    )
    inv.set_schema_store(schema_store)
    return inv


@pytest.mark.asyncio
async def test_hook_observes_successful_cp_direct_tool_call(
    invoker, schema_store, mock_mcp_manager
):
    # SH-01: successful CP-direct call fires the hook and a schema is recorded.
    from ploston_core.invoker.invoker import ToolCallResult

    # Use the _invoke_mcp result shape by calling through invoke().
    # Patch _invoke_mcp to return a concrete ToolCallResult.
    invoker._invoke_mcp = AsyncMock(
        return_value=ToolCallResult(
            success=True,
            output={"id": 1, "repo": "ploston"},
            duration_ms=5,
            tool_name="github__get_repo",
        )
    )

    await invoker.invoke("github__get_repo", {})
    # Give the fire-and-forget task a chance to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    schema = schema_store.get("github", "get_repo")
    assert schema is not None
    assert "id" in schema.success_schema.properties


@pytest.mark.asyncio
async def test_hook_self_resolves_runner_prefixed_tool_name():
    # SH-02: runner-prefixed tool names feed into server__tool store keys.
    schema_store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value={"ok": True})

    inv = ToolInvoker(
        tool_registry=MagicMock(),
        mcp_manager=MagicMock(),
        sandbox_factory=MagicMock(),
        runner_dispatcher=dispatcher,
    )
    inv.set_schema_store(schema_store)

    await inv.invoke("macbook-pro-local__github__actions_list", {})
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert schema_store.get("github", "actions_list") is not None


@pytest.mark.asyncio
async def test_hook_does_not_fire_on_failure():
    # SH-03: failed results never populate the store.
    schema_store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("boom"))

    inv = ToolInvoker(
        tool_registry=MagicMock(),
        mcp_manager=MagicMock(),
        sandbox_factory=MagicMock(),
        runner_dispatcher=dispatcher,
    )
    inv.set_schema_store(schema_store)

    result = await inv.invoke("macbook-pro-local__github__actions_list", {})
    assert result.success is False
    await asyncio.sleep(0)

    assert schema_store.get("github", "actions_list") is None


@pytest.mark.asyncio
async def test_hook_is_noop_without_schema_store_wired(
    mock_registry, mock_mcp_manager, mock_sandbox_factory
):
    # SH-04: unwired ToolInvoker never touches a store; invoke still succeeds.
    from ploston_core.invoker.invoker import ToolCallResult

    inv = ToolInvoker(
        tool_registry=mock_registry,
        mcp_manager=mock_mcp_manager,
        sandbox_factory=mock_sandbox_factory,
    )
    inv._invoke_mcp = AsyncMock(
        return_value=ToolCallResult(success=True, output={"x": 1}, duration_ms=1, tool_name="noop")
    )

    result = await inv.invoke("noop", {})
    assert result.success is True
    # schema_store remains None -- no error, no side-effects.
    assert inv._schema_store is None
