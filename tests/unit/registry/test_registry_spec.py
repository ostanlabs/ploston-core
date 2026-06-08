"""Spec tests for ``ToolRegistry`` (registry.py).

These tests assert the registry's *documented* contract — registration via
refresh, lookup/resolution, listing/filtering, conflict/update handling, tag
injection, schema storage, system-tool registration, and error cases — as
described in the docstrings of ``ToolRegistry`` and ``registry/types.py``.

Only the external boundaries (``MCPClientManager`` and the optional
``on_tools_changed`` callback / metrics / schema store) are mocked. The
registry's own logic is exercised for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.config.models import SystemToolsConfig, ToolsConfig
from ploston_core.errors import AELError
from ploston_core.mcp.types import ToolSchema
from ploston_core.registry import ToolRegistry
from ploston_core.registry.types import RefreshResult, ToolDefinition, ToolRouter
from ploston_core.types import ToolSource, ToolStatus

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _schema(
    name: str,
    description: str = "desc",
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        input_schema=input_schema if input_schema is not None else {"type": "object"},
        output_schema=output_schema,
    )


def _make_manager(
    refresh_all_return: dict | None = None,
    connections: dict | None = None,
) -> MagicMock:
    """Build a mocked MCPClientManager.

    Only the methods ``ToolRegistry`` calls are stubbed; the registry boundary
    is the manager, so this is the correct mock surface.
    """
    mgr = MagicMock()
    mgr.connect_all = AsyncMock(return_value={})
    mgr.refresh_all = AsyncMock(return_value=refresh_all_return or {})
    mgr.on_config_change = AsyncMock(return_value=None)
    conns = connections or {}
    mgr.get_connection = MagicMock(side_effect=lambda name: conns.get(name))
    return mgr


def _make_registry(
    refresh_all_return: dict | None = None,
    connections: dict | None = None,
    config: ToolsConfig | None = None,
    on_tools_changed=None,
) -> ToolRegistry:
    mgr = _make_manager(refresh_all_return, connections)
    return ToolRegistry(
        mcp_manager=mgr,
        config=config if config is not None else ToolsConfig(),
        logger=None,
        on_tools_changed=on_tools_changed,
    )


# ---------------------------------------------------------------------------
# get / get_or_raise — lookup by name + not-found error case
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown_tool():
    reg = _make_registry()
    assert reg.get("nope") is None


def test_get_returns_stored_tool():
    reg = _make_registry()
    tool = ToolDefinition(name="t", description="d", source=ToolSource.MCP)
    reg._tools["t"] = tool
    assert reg.get("t") is tool


def test_get_or_raise_returns_tool_when_present():
    reg = _make_registry()
    tool = ToolDefinition(name="t", description="d", source=ToolSource.MCP)
    reg._tools["t"] = tool
    assert reg.get_or_raise("t") is tool


def test_get_or_raise_raises_tool_unavailable_when_missing():
    """Contract: raises AELError(TOOL_UNAVAILABLE) when the tool is absent."""
    reg = _make_registry()
    with pytest.raises(AELError) as exc:
        reg.get_or_raise("ghost")
    # The error code must be TOOL_UNAVAILABLE per the docstring.
    assert exc.value.code == "TOOL_UNAVAILABLE"
    # The registry threads the missing tool name into the error detail.
    assert exc.value.detail is not None and "ghost" in exc.value.detail


# ---------------------------------------------------------------------------
# Registration via refresh — add / update / source routing / tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_adds_new_mcp_tools():
    reg = _make_registry(
        refresh_all_return={"github": [_schema("get_repo"), _schema("list_repos")]}
    )
    result = await reg.refresh()

    assert isinstance(result, RefreshResult)
    assert set(result.added) == {"get_repo", "list_repos"}
    assert result.removed == []
    assert result.updated == []
    assert result.total_tools == 2

    tool = reg.get("get_repo")
    assert tool is not None
    assert tool.source == ToolSource.MCP
    assert tool.server_name == "github"
    assert tool.status == ToolStatus.AVAILABLE
    assert tool.last_seen is not None


@pytest.mark.asyncio
async def test_refresh_routes_native_tools_server_to_native_source():
    """``native_tools``/``native-tools`` servers map to ToolSource.NATIVE."""
    for server in ("native_tools", "native-tools"):
        reg = _make_registry(refresh_all_return={server: [_schema("read_file")]})
        await reg.refresh()
        tool = reg.get("read_file")
        assert tool is not None
        assert tool.source == ToolSource.NATIVE


@pytest.mark.asyncio
async def test_refresh_injects_system_tags():
    """DEC-170: refreshed tools get kind/source/server tags."""
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo")]})
    await reg.refresh()
    tool = reg.get("get_repo")
    assert tool is not None
    assert tool.tags == {"kind:tool", "source:mcp", "server:github"}


@pytest.mark.asyncio
async def test_refresh_updates_existing_tool_on_description_change():
    """Re-seeing a tool with a changed description marks it updated, not added."""
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo", "v1")]})
    await reg.refresh()

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": [_schema("get_repo", "v2")]})
    result = await reg.refresh()

    assert result.added == []
    assert result.updated == ["get_repo"]
    assert reg.get("get_repo").description == "v2"
    # Identity preserved (update in place, not replace).
    assert result.total_tools == 1


@pytest.mark.asyncio
async def test_refresh_updates_on_input_schema_change():
    reg = _make_registry(
        refresh_all_return={"github": [_schema("get_repo", input_schema={"a": 1})]}
    )
    await reg.refresh()
    reg._mcp_manager.refresh_all = AsyncMock(
        return_value={"github": [_schema("get_repo", input_schema={"a": 2})]}
    )
    result = await reg.refresh()
    assert result.updated == ["get_repo"]


@pytest.mark.asyncio
async def test_refresh_no_change_is_not_reported_as_updated():
    """An identical re-observation must not be flagged as updated."""
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo", "same")]})
    await reg.refresh()
    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": [_schema("get_repo", "same")]})
    result = await reg.refresh()
    assert result.added == []
    assert result.updated == []
    assert result.removed == []


@pytest.mark.asyncio
async def test_refresh_marks_vanished_mcp_tool_unavailable_not_deleted():
    """Contract: removed MCP/NATIVE tools are marked UNAVAILABLE, not deleted."""
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo")]})
    await reg.refresh()

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": []})
    result = await reg.refresh()

    assert result.removed == ["get_repo"]
    # Still present in the registry, but unavailable.
    tool = reg.get("get_repo")
    assert tool is not None
    assert tool.status == ToolStatus.UNAVAILABLE
    # total_tools counts all tracked tools, including unavailable ones.
    assert result.total_tools == 1


@pytest.mark.asyncio
async def test_refresh_resurrects_unavailable_tool_to_available():
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo")]})
    await reg.refresh()
    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": []})
    await reg.refresh()
    assert reg.get("get_repo").status == ToolStatus.UNAVAILABLE

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": [_schema("get_repo")]})
    await reg.refresh()
    tool = reg.get("get_repo")
    assert tool.status == ToolStatus.AVAILABLE
    assert tool.error is None


@pytest.mark.asyncio
async def test_refresh_does_not_unavailable_system_tools():
    """System tools are not MCP/NATIVE, so a refresh must never disable them."""
    reg = _make_registry()
    reg._register_system_tools()  # adds python_exec (SYSTEM)
    assert reg.get("python_exec").status == ToolStatus.AVAILABLE

    result = await reg.refresh()  # no MCP tools at all
    assert "python_exec" not in result.removed
    assert reg.get("python_exec").status == ToolStatus.AVAILABLE


# ---------------------------------------------------------------------------
# Name-conflict / same-name-from-different-server behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_tool_name_from_second_server_updates_source_and_server():
    """Tools are keyed by bare name; a same-named tool from another server
    updates the existing record (source may change, server stays as first add).

    This pins the *actual documented* behavior: the existing entry's source is
    overwritten ("Update source in case it changed"), but server_name is set
    only at creation and is NOT rewritten on update.
    """
    reg = _make_registry(refresh_all_return={"github": [_schema("shared")]})
    await reg.refresh()
    assert reg.get("shared").server_name == "github"

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"native_tools": [_schema("shared")]})
    await reg.refresh()
    tool = reg.get("shared")
    # Source is updated to reflect the latest-seen server's source...
    assert tool.source == ToolSource.NATIVE
    # ...but server_name remains the original (only set on creation).
    assert tool.server_name == "github"
    assert reg.get_router("shared").source == ToolSource.NATIVE


# ---------------------------------------------------------------------------
# refresh_server — single-server refresh + server-not-found error case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_server_unknown_returns_error_result():
    """Contract: unknown server yields a RefreshResult carrying the error."""
    reg = _make_registry(connections={})
    result = await reg.refresh_server("missing")
    assert result.added == []
    assert result.errors == {"missing": "Server not found"}
    assert result.total_tools == 0


@pytest.mark.asyncio
async def test_refresh_server_adds_tools_from_that_server():
    conn = MagicMock()
    conn.refresh_tools = AsyncMock(return_value=[_schema("ping"), _schema("pong")])
    reg = _make_registry(connections={"srv": conn})
    result = await reg.refresh_server("srv")
    assert set(result.added) == {"ping", "pong"}
    assert reg.get("ping").server_name == "srv"
    assert reg.get("ping").tags == {"kind:tool", "source:mcp", "server:srv"}


@pytest.mark.asyncio
async def test_refresh_server_updates_existing_and_skips_unchanged():
    conn = MagicMock()
    conn.refresh_tools = AsyncMock(return_value=[_schema("ping", "v1")])
    reg = _make_registry(connections={"srv": conn})
    await reg.refresh_server("srv")

    conn.refresh_tools = AsyncMock(return_value=[_schema("ping", "v2")])
    result = await reg.refresh_server("srv")
    assert result.updated == ["ping"]
    assert result.added == []


@pytest.mark.asyncio
async def test_refresh_server_marks_only_its_own_vanished_tools_unavailable():
    """refresh_server must only disable tools belonging to that server."""
    # Seed two servers via full refresh.
    reg = _make_registry(
        refresh_all_return={
            "srv_a": [_schema("a_tool")],
            "srv_b": [_schema("b_tool")],
        }
    )
    await reg.refresh()

    # Now refresh srv_a with an empty list — only a_tool should go unavailable.
    conn_a = MagicMock()
    conn_a.refresh_tools = AsyncMock(return_value=[])
    reg._mcp_manager.get_connection = MagicMock(
        side_effect=lambda n: conn_a if n == "srv_a" else None
    )
    result = await reg.refresh_server("srv_a")

    assert result.removed == ["a_tool"]
    assert reg.get("a_tool").status == ToolStatus.UNAVAILABLE
    assert reg.get("b_tool").status == ToolStatus.AVAILABLE


@pytest.mark.asyncio
async def test_refresh_server_captures_connection_exception():
    """A connection raising during refresh is recorded in errors, not propagated."""
    conn = MagicMock()
    conn.refresh_tools = AsyncMock(side_effect=RuntimeError("boom"))
    reg = _make_registry(connections={"srv": conn})
    result = await reg.refresh_server("srv")
    assert "srv" in result.errors
    assert "boom" in result.errors["srv"]
    assert result.added == []


# ---------------------------------------------------------------------------
# on_tools_changed callback firing semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_fires_when_tools_added():
    cb = AsyncMock()
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo")]}, on_tools_changed=cb)
    await reg.refresh()
    cb.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_not_fired_when_nothing_changed():
    """Contract in _fire_tools_changed: only fire on add/remove/update."""
    cb = AsyncMock()
    reg = _make_registry(
        refresh_all_return={"github": [_schema("get_repo", "same")]},
        on_tools_changed=cb,
    )
    await reg.refresh()  # initial add -> fires
    cb.reset_mock()
    reg._mcp_manager.refresh_all = AsyncMock(return_value={"github": [_schema("get_repo", "same")]})
    await reg.refresh()  # no change -> must not fire
    cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_server_fires_callback_on_change():
    cb = AsyncMock()
    conn = MagicMock()
    conn.refresh_tools = AsyncMock(return_value=[_schema("ping")])
    reg = _make_registry(connections={"srv": conn}, on_tools_changed=cb)
    await reg.refresh_server("srv")
    cb.assert_awaited_once()


# ---------------------------------------------------------------------------
# Listing / filtering
# ---------------------------------------------------------------------------


def _seed_mixed(reg: ToolRegistry) -> None:
    reg._tools = {
        "mcp_a": ToolDefinition(
            name="mcp_a",
            description="mcp available alpha",
            source=ToolSource.MCP,
            server_name="github",
            status=ToolStatus.AVAILABLE,
            tags={"kind:tool", "source:mcp", "server:github"},
        ),
        "mcp_b": ToolDefinition(
            name="mcp_b",
            description="mcp unavailable beta",
            source=ToolSource.MCP,
            server_name="slack",
            status=ToolStatus.UNAVAILABLE,
            tags={"kind:tool", "source:mcp", "server:slack"},
        ),
        "sys_c": ToolDefinition(
            name="sys_c",
            description="system gamma",
            source=ToolSource.SYSTEM,
            server_name="system",
            status=ToolStatus.AVAILABLE,
            tags={"kind:tool", "source:system", "server:system"},
        ),
    }


def test_list_tools_no_filter_returns_all():
    reg = _make_registry()
    _seed_mixed(reg)
    assert len(reg.list_tools()) == 3


def test_list_tools_filter_by_source():
    reg = _make_registry()
    _seed_mixed(reg)
    names = {t.name for t in reg.list_tools(source=ToolSource.MCP)}
    assert names == {"mcp_a", "mcp_b"}


def test_list_tools_filter_by_server():
    reg = _make_registry()
    _seed_mixed(reg)
    names = {t.name for t in reg.list_tools(server_name="slack")}
    assert names == {"mcp_b"}


def test_list_tools_filter_by_status():
    reg = _make_registry()
    _seed_mixed(reg)
    names = {t.name for t in reg.list_tools(status=ToolStatus.AVAILABLE)}
    assert names == {"mcp_a", "sys_c"}


def test_list_tools_tag_filter_is_match_all():
    """Tag filter is subset/match-all: a tool must carry ALL requested tags."""
    reg = _make_registry()
    _seed_mixed(reg)
    # Only mcp_a has BOTH source:mcp AND server:github.
    got = reg.list_tools(tags={"source:mcp", "server:github"})
    assert {t.name for t in got} == {"mcp_a"}
    # A tag no tool has -> empty.
    assert reg.list_tools(tags={"server:nonexistent"}) == []


def test_list_tools_combined_filters_are_anded():
    reg = _make_registry()
    _seed_mixed(reg)
    got = reg.list_tools(source=ToolSource.MCP, status=ToolStatus.AVAILABLE)
    assert {t.name for t in got} == {"mcp_a"}


def test_list_available_returns_only_available():
    reg = _make_registry()
    _seed_mixed(reg)
    names = {t.name for t in reg.list_available()}
    assert names == {"mcp_a", "sys_c"}


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_matches_name_case_insensitive():
    reg = _make_registry()
    _seed_mixed(reg)
    got = {t.name for t in reg.search("MCP_A")}
    assert got == {"mcp_a"}


def test_search_matches_description_substring():
    reg = _make_registry()
    _seed_mixed(reg)
    got = {t.name for t in reg.search("gamma")}
    assert got == {"sys_c"}


def test_search_empty_query_matches_all():
    """Empty substring is contained in every string, so all tools match."""
    reg = _make_registry()
    _seed_mixed(reg)
    assert len(reg.search("")) == 3


def test_search_no_match_returns_empty():
    reg = _make_registry()
    _seed_mixed(reg)
    assert reg.search("zzz-nope") == []


# ---------------------------------------------------------------------------
# get_router — routing resolution + not-found
# ---------------------------------------------------------------------------


def test_get_router_returns_source_and_server():
    reg = _make_registry()
    _seed_mixed(reg)
    router = reg.get_router("mcp_a")
    assert isinstance(router, ToolRouter)
    assert router.source == ToolSource.MCP
    assert router.server_name == "github"


def test_get_router_returns_none_for_unknown():
    reg = _make_registry()
    assert reg.get_router("ghost") is None


# ---------------------------------------------------------------------------
# get_for_mcp_exposure — only available + source filtering
# ---------------------------------------------------------------------------


def test_get_for_mcp_exposure_returns_only_available_in_mcp_format():
    reg = _make_registry()
    _seed_mixed(reg)
    exposed = reg.get_for_mcp_exposure()
    names = {t["name"] for t in exposed}
    # mcp_b is unavailable -> excluded.
    assert names == {"mcp_a", "sys_c"}
    # MCP format keys present.
    for t in exposed:
        assert "name" in t and "description" in t and "inputSchema" in t


def test_get_for_mcp_exposure_filters_by_internal_source():
    from ploston_core.types.internal import InternalToolSource

    reg = _make_registry()
    _seed_mixed(reg)
    exposed = reg.get_for_mcp_exposure(sources=[InternalToolSource.SYSTEM])
    assert {t["name"] for t in exposed} == {"sys_c"}


def test_get_for_mcp_exposure_empty_source_filter_returns_nothing():
    reg = _make_registry()
    _seed_mixed(reg)
    # An empty list is *not* None, so the filter applies and matches nothing.
    assert reg.get_for_mcp_exposure(sources=[]) == []


# ---------------------------------------------------------------------------
# System tool registration
# ---------------------------------------------------------------------------


def test_register_system_tools_adds_python_exec_when_enabled():
    reg = _make_registry(config=ToolsConfig())  # python_exec enabled by default
    reg._register_system_tools()
    tool = reg.get("python_exec")
    assert tool is not None
    assert tool.source == ToolSource.SYSTEM
    assert tool.server_name == "system"
    assert tool.status == ToolStatus.AVAILABLE
    assert tool.tags == {"kind:tool", "source:system", "server:system"}
    assert "code" in tool.input_schema["required"]


def test_register_system_tools_skips_python_exec_when_disabled():
    cfg = ToolsConfig(system_tools=SystemToolsConfig(python_exec_enabled=False))
    reg = _make_registry(config=cfg)
    reg._register_system_tools()
    assert reg.get("python_exec") is None


# ---------------------------------------------------------------------------
# initialize / on_config_change — orchestration contracts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_registers_system_tools_then_connects_and_refreshes():
    reg = _make_registry(refresh_all_return={"github": [_schema("get_repo")]})
    result = await reg.initialize()

    # System tools registered (python_exec) + MCP tool from refresh.
    assert reg.get("python_exec") is not None
    assert reg.get("get_repo") is not None
    reg._mcp_manager.connect_all.assert_awaited_once()
    # total_tools reflects both.
    assert result.total_tools == 2


@pytest.mark.asyncio
async def test_on_config_change_updates_config_and_refreshes():
    reg = _make_registry(refresh_all_return={})
    new_cfg = ToolsConfig(system_tools=SystemToolsConfig(python_exec_enabled=False))
    result = await reg.on_config_change(new_cfg)

    # Config swapped on the registry.
    assert reg._config is new_cfg
    # Manager notified.
    reg._mcp_manager.on_config_change.assert_awaited_once_with(new_cfg)
    # Disabled system tool not registered.
    assert reg.get("python_exec") is None
    assert isinstance(result, RefreshResult)


# ---------------------------------------------------------------------------
# Metrics boundary — set_metrics + counting by available source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_updated_with_available_source_counts():
    reg = _make_registry(
        refresh_all_return={
            "github": [_schema("g1"), _schema("g2")],
            "native_tools": [_schema("n1")],
        }
    )
    metrics = MagicMock()
    reg.set_metrics(metrics)
    await reg.refresh()

    metrics.update_tools_by_source.assert_called()
    kwargs = metrics.update_tools_by_source.call_args.kwargs
    assert kwargs["mcp_tools"] == 2
    assert kwargs["native_tools"] == 1
    # No system tools registered in this path.
    assert kwargs["system_tools"] == 0


def test_metrics_noop_when_unset():
    """_update_metrics must be a safe no-op when no metrics are wired."""
    reg = _make_registry()
    _seed_mixed(reg)
    # Should not raise.
    reg._update_metrics()
