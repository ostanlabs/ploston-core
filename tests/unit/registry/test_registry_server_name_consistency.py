"""Regression tests for R-4 (DECISION D3): server_name consistency on refresh.

BUG R-4: On ``refresh``, when a bare-name tool reappears from a *different*
MCP server, the registry updated the tool's ``source`` in place ("Update
source in case it changed") but left ``server_name`` at its first-seen value.
``get_router`` could then return a ``(source, server_name)`` pair pointing at
two different servers -- an inconsistent routing record.

FIX (D3): when a tool with the same bare name is refreshed from a different
server, update ``server_name`` alongside ``source`` so the routing record
stays internally consistent and routes to the latest-seen server.

These tests assert the *corrected* contract. They are RED before the fix
(server_name stays at the first-seen value) and GREEN after.

Tests live in a new file (not ``test_registry_spec.py``) per ownership rules.
The mock surface mirrors the spec helpers: only the ``MCPClientManager``
boundary is stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.config.models import ToolsConfig
from ploston_core.mcp.types import ToolSchema
from ploston_core.registry import ToolRegistry
from ploston_core.types import ToolSource, ToolStatus


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


def _make_manager(refresh_all_return: dict | None = None) -> MagicMock:
    mgr = MagicMock()
    mgr.connect_all = AsyncMock(return_value={})
    mgr.refresh_all = AsyncMock(return_value=refresh_all_return or {})
    mgr.on_config_change = AsyncMock(return_value=None)
    mgr.get_connection = MagicMock(return_value=None)
    return mgr


def _make_registry(refresh_all_return: dict | None = None) -> ToolRegistry:
    return ToolRegistry(
        mcp_manager=_make_manager(refresh_all_return),
        config=ToolsConfig(),
        logger=None,
    )


# ---------------------------------------------------------------------------
# Core R-4 fix: server_name follows the latest-seen server.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_updates_server_name_when_tool_moves_servers():
    """Register tool X from server A; refresh X from server B.

    After the fix, the registry entry's ``server_name`` must be B (and
    ``source`` consistent with B), so routing follows the latest-seen server.
    """
    # Server A == "github" -> ToolSource.MCP
    reg = _make_registry(refresh_all_return={"server_a": [_schema("X")]})
    await reg.refresh()
    tool = reg.get("X")
    assert tool is not None
    assert tool.server_name == "server_a"
    assert tool.source == ToolSource.MCP

    # Refresh: same bare name now comes from a DIFFERENT server.
    reg._mcp_manager.refresh_all = AsyncMock(return_value={"server_b": [_schema("X")]})
    await reg.refresh()

    tool = reg.get("X")
    assert tool is not None
    # server_name must follow the latest-seen server (the fix).
    assert tool.server_name == "server_b"
    # source remains consistent (both are plain MCP servers).
    assert tool.source == ToolSource.MCP


@pytest.mark.asyncio
async def test_get_router_routes_to_latest_server_after_move():
    """get_router must return a consistent (source, server_name) pair pointing
    at the latest-seen server -- not a split record."""
    reg = _make_registry(refresh_all_return={"alpha": [_schema("X")]})
    await reg.refresh()
    assert reg.get_router("X").server_name == "alpha"

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"beta": [_schema("X")]})
    await reg.refresh()

    router = reg.get_router("X")
    assert router is not None
    assert router.server_name == "beta"
    assert router.source == ToolSource.MCP


@pytest.mark.asyncio
async def test_refresh_source_and_server_name_stay_consistent_mcp_to_native():
    """When a tool moves to the native_tools server, BOTH source and
    server_name update together (source -> NATIVE, server_name -> native_tools).

    This guards against the half-update bug where source flips to NATIVE but
    server_name is left pointing at the old MCP server.
    """
    reg = _make_registry(refresh_all_return={"github": [_schema("shared")]})
    await reg.refresh()
    assert reg.get("shared").source == ToolSource.MCP
    assert reg.get("shared").server_name == "github"

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"native_tools": [_schema("shared")]})
    await reg.refresh()

    tool = reg.get("shared")
    assert tool.source == ToolSource.NATIVE
    assert tool.server_name == "native_tools"
    # Tags (DEC-170) are rebuilt from the updated source/server, so they must
    # reflect the new server too -- no stale server:github tag.
    assert tool.tags == {"kind:tool", "source:native", "server:native_tools"}


# ---------------------------------------------------------------------------
# No-regression: bare-name keying / conflict behavior is preserved.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bare_name_keying_preserved_single_entry_after_move():
    """Tools remain keyed by bare name: a same-named tool from another server
    updates the single existing record rather than creating a second entry."""
    reg = _make_registry(refresh_all_return={"server_a": [_schema("X")]})
    await reg.refresh()

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"server_b": [_schema("X")]})
    result = await reg.refresh()

    # Exactly one record for the bare name.
    assert len([t for t in reg.list_tools() if t.name == "X"]) == 1
    assert result.total_tools == 1
    # It's an update of the existing record, not a fresh add.
    assert result.added == []


@pytest.mark.asyncio
async def test_tool_stays_available_after_server_move():
    """A tool that moves servers within a single refresh stays AVAILABLE and is
    not reported as removed (its bare name is present in the new tool set)."""
    reg = _make_registry(refresh_all_return={"server_a": [_schema("X")]})
    await reg.refresh()

    reg._mcp_manager.refresh_all = AsyncMock(return_value={"server_b": [_schema("X")]})
    result = await reg.refresh()

    assert reg.get("X").status == ToolStatus.AVAILABLE
    assert "X" not in result.removed
