"""Spec tests for MCPClientManager (ploston_core.mcp.manager).

These tests assert the *contracted* behavior of the manager (per docstrings):
- connect_all: connects in parallel, failures don't stop others, returns status map
- disconnect_all: disconnects all, clears connections, honours timeout
- refresh_all: refreshes only connected servers, isolates per-server failures
- call_tool: TOOL_UNAVAILABLE when server missing, else delegates to connection
- get_all_tools / get_status / get_connection / list_connections
- on_config_change: add / remove / reconnect-on-change semantics
- _handle_tools_changed: propagates to manager callback, swallows callback errors

The unit under test is the manager. The mocked boundary is MCPConnection,
which the manager constructs and delegates to. We patch
``ploston_core.mcp.manager.MCPConnection`` so no real transport/process is used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.config.models import MCPServerDefinition, ToolsConfig
from ploston_core.errors.errors import AELError
from ploston_core.mcp.manager import MCPClientManager
from ploston_core.mcp.types import MCPCallResult, ServerStatus, ToolSchema
from ploston_core.types import ConnectionStatus, MCPTransport

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _server_def(command: str = "echo hi", **kw) -> MCPServerDefinition:
    return MCPServerDefinition(command=command, transport=MCPTransport.STDIO, **kw)


def _config(**servers: MCPServerDefinition) -> ToolsConfig:
    return ToolsConfig(mcp_servers=dict(servers))


def _make_fake_conn(
    name: str,
    *,
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    tools: list[ToolSchema] | None = None,
) -> MagicMock:
    """Build a MagicMock standing in for an MCPConnection.

    The real MCPConnection exposes: connect(), disconnect(), refresh_tools(),
    call_tool(), get_status(), get_connection helpers, list_tools(), and a
    ``status`` property whose ``.value`` the manager inspects.
    """
    tools = tools or []
    conn = MagicMock(name=f"conn:{name}")
    conn.name = name
    # status property — manager reads conn.status.value
    conn.status = status
    conn.connect = AsyncMock(return_value=None)
    conn.disconnect = AsyncMock(return_value=None)
    conn.refresh_tools = AsyncMock(return_value=tools)
    conn.list_tools = MagicMock(return_value=tools)
    conn.call_tool = AsyncMock()
    conn.get_status = MagicMock(
        return_value=ServerStatus(
            name=name,
            status=status,
            tools=[t.name for t in tools],
        )
    )
    return conn


def _patch_connection_factory(conns: dict[str, MagicMock]):
    """Patch manager.MCPConnection so each named server yields a fake conn.

    Returns the patch context manager.
    """

    def _factory(name, config, **kwargs):
        # capture the callback the manager registers so we can drive it
        conn = conns[name]
        conn._registered_on_tools_changed = kwargs.get("on_tools_changed")
        conn._registered_log_file = kwargs.get("log_file")
        return conn

    return patch("ploston_core.mcp.manager.MCPConnection", side_effect=_factory)


# ---------------------------------------------------------------------------
# connect_all
# ---------------------------------------------------------------------------


async def test_connect_all_empty_config_returns_empty_dict():
    mgr = MCPClientManager(config=_config())
    result = await mgr.connect_all()
    assert result == {}
    assert mgr.list_connections() == []


async def test_connect_all_connects_each_server_and_returns_status_map():
    conns = {
        "a": _make_fake_conn("a", tools=[ToolSchema("t1", "d", {})]),
        "b": _make_fake_conn("b"),
    }
    mgr = MCPClientManager(config=_config(a=_server_def(), b=_server_def()))
    with _patch_connection_factory(conns):
        status = await mgr.connect_all()

    conns["a"].connect.assert_awaited_once()
    conns["b"].connect.assert_awaited_once()
    assert set(status.keys()) == {"a", "b"}
    assert status["a"].status == ConnectionStatus.CONNECTED


async def test_connect_all_one_failure_does_not_stop_others():
    """Contract: 'Failures are logged but don't stop others.'"""
    good = _make_fake_conn("good")
    bad = _make_fake_conn("bad", status=ConnectionStatus.ERROR)
    bad.connect = AsyncMock(side_effect=RuntimeError("boom"))
    conns = {"good": good, "bad": bad}
    mgr = MCPClientManager(config=_config(good=_server_def(), bad=_server_def()))
    with _patch_connection_factory(conns):
        status = await mgr.connect_all()

    # good still got connected despite bad raising
    good.connect.assert_awaited_once()
    assert set(status.keys()) == {"good", "bad"}
    # status comes from get_status() — bad reports ERROR
    assert status["bad"].status == ConnectionStatus.ERROR
    assert status["good"].status == ConnectionStatus.CONNECTED


async def test_connect_all_is_idempotent_for_existing_connections():
    """Second connect_all must not recreate connections already present."""
    conn = _make_fake_conn("a")
    mgr = MCPClientManager(config=_config(a=_server_def()))
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=conn) as ctor:
        await mgr.connect_all()
        await mgr.connect_all()
    # MCPConnection constructed only once across two connect_all calls
    assert ctor.call_count == 1
    # but connect() attempted on each pass
    assert conn.connect.await_count == 2


async def test_connect_all_passes_log_file_when_log_dir_set(tmp_path):
    conn = _make_fake_conn("srv")
    mgr = MCPClientManager(config=_config(srv=_server_def()), log_dir=tmp_path)
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=conn) as ctor:
        await mgr.connect_all()
    _, kwargs = ctor.call_args
    assert kwargs["log_file"] == tmp_path / "srv.log"


async def test_mcp_log_file_none_when_no_log_dir():
    conn = _make_fake_conn("srv")
    mgr = MCPClientManager(config=_config(srv=_server_def()))
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=conn) as ctor:
        await mgr.connect_all()
    _, kwargs = ctor.call_args
    assert kwargs["log_file"] is None


# ---------------------------------------------------------------------------
# call_tool
# ---------------------------------------------------------------------------


async def test_call_tool_server_not_found_raises_tool_unavailable():
    mgr = MCPClientManager(config=_config())
    with pytest.raises(AELError) as ei:
        await mgr.call_tool("missing", "some_tool", {})
    assert ei.value.code == "TOOL_UNAVAILABLE"
    assert ei.value.tool_name == "some_tool"


async def test_call_tool_delegates_to_connection():
    conn = _make_fake_conn("srv")
    expected = MCPCallResult(success=True, content="ok", raw_response={}, duration_ms=1)
    conn.call_tool = AsyncMock(return_value=expected)
    mgr = MCPClientManager(config=_config(srv=_server_def()))
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=conn):
        await mgr.connect_all()
    result = await mgr.call_tool("srv", "tool_x", {"a": 1}, timeout_seconds=7)
    assert result is expected
    conn.call_tool.assert_awaited_once_with("tool_x", {"a": 1}, 7)


# ---------------------------------------------------------------------------
# get_connection / list_connections / get_all_tools / get_status
# ---------------------------------------------------------------------------


async def test_get_connection_returns_none_when_absent():
    mgr = MCPClientManager(config=_config())
    assert mgr.get_connection("nope") is None


async def test_get_all_tools_only_includes_connected_servers():
    connected = _make_fake_conn("up", tools=[ToolSchema("t1", "d", {})])
    down = _make_fake_conn("down", status=ConnectionStatus.ERROR, tools=[ToolSchema("t2", "d", {})])
    conns = {"up": connected, "down": down}
    mgr = MCPClientManager(config=_config(up=_server_def(), down=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    all_tools = mgr.get_all_tools()
    assert "up" in all_tools
    assert "down" not in all_tools
    assert [t.name for t in all_tools["up"]] == ["t1"]


async def test_get_status_returns_status_for_all_servers():
    conns = {"a": _make_fake_conn("a"), "b": _make_fake_conn("b", status=ConnectionStatus.ERROR)}
    mgr = MCPClientManager(config=_config(a=_server_def(), b=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    status = mgr.get_status()
    assert set(status.keys()) == {"a", "b"}
    assert status["b"].status == ConnectionStatus.ERROR


async def test_list_connections_returns_all_connection_objects():
    conns = {"a": _make_fake_conn("a"), "b": _make_fake_conn("b")}
    mgr = MCPClientManager(config=_config(a=_server_def(), b=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    listed = mgr.list_connections()
    assert set(listed) == {conns["a"], conns["b"]}


# ---------------------------------------------------------------------------
# refresh_all
# ---------------------------------------------------------------------------


async def test_refresh_all_only_refreshes_connected_servers():
    up = _make_fake_conn("up", tools=[ToolSchema("t1", "d", {})])
    down = _make_fake_conn("down", status=ConnectionStatus.DISCONNECTED)
    conns = {"up": up, "down": down}
    mgr = MCPClientManager(config=_config(up=_server_def(), down=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    result = await mgr.refresh_all()
    up.refresh_tools.assert_awaited_once()
    down.refresh_tools.assert_not_awaited()
    assert "up" in result and "down" not in result
    assert [t.name for t in result["up"]] == ["t1"]


async def test_refresh_all_isolates_per_server_failure():
    """A server raising during refresh must yield [] for that server, not abort."""
    ok = _make_fake_conn("ok", tools=[ToolSchema("a", "d", {})])
    boom = _make_fake_conn("boom")
    boom.refresh_tools = AsyncMock(side_effect=RuntimeError("nope"))
    conns = {"ok": ok, "boom": boom}
    mgr = MCPClientManager(config=_config(ok=_server_def(), boom=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    result = await mgr.refresh_all()
    assert [t.name for t in result["ok"]] == ["a"]
    assert result["boom"] == []


# ---------------------------------------------------------------------------
# disconnect_all
# ---------------------------------------------------------------------------


async def test_disconnect_all_disconnects_and_clears():
    conns = {"a": _make_fake_conn("a"), "b": _make_fake_conn("b")}
    mgr = MCPClientManager(config=_config(a=_server_def(), b=_server_def()))
    with _patch_connection_factory(conns):
        await mgr.connect_all()
    await mgr.disconnect_all()
    conns["a"].disconnect.assert_awaited_once()
    conns["b"].disconnect.assert_awaited_once()
    assert mgr.list_connections() == []


async def test_disconnect_all_timeout_still_clears_connections():
    """Contract: on timeout it logs a warning but must still clear state."""

    async def _hang():
        import asyncio

        await asyncio.sleep(10)

    conn = _make_fake_conn("slow")
    conn.disconnect = AsyncMock(side_effect=_hang)
    mgr = MCPClientManager(config=_config(slow=_server_def()))
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=conn):
        await mgr.connect_all()
    await mgr.disconnect_all(timeout=0.05)
    assert mgr.list_connections() == []


# ---------------------------------------------------------------------------
# _handle_tools_changed callback propagation
# ---------------------------------------------------------------------------


async def test_handle_tools_changed_propagates_to_manager_callback():
    received: list[tuple[str, list[ToolSchema]]] = []
    mgr = MCPClientManager(
        config=_config(),
        on_tools_changed=lambda name, tools: received.append((name, tools)),
    )
    tools = [ToolSchema("t", "d", {})]
    mgr._handle_tools_changed("srv1", tools)
    assert received == [("srv1", tools)]


async def test_handle_tools_changed_swallows_callback_errors():
    """A throwing manager callback must not propagate out of _handle_tools_changed."""

    def _boom(name, tools):
        raise ValueError("callback broke")

    mgr = MCPClientManager(config=_config(), on_tools_changed=_boom)
    # Must not raise
    mgr._handle_tools_changed("srv", [])


async def test_connection_receives_manager_tools_changed_handler():
    """The manager must register its _handle_tools_changed on each connection."""
    conn = _make_fake_conn("srv")
    mgr = MCPClientManager(config=_config(srv=_server_def()))
    with _patch_connection_factory({"srv": conn}):
        await mgr.connect_all()
    assert conn._registered_on_tools_changed == mgr._handle_tools_changed


# ---------------------------------------------------------------------------
# on_config_change
# ---------------------------------------------------------------------------


async def test_on_config_change_adds_new_server():
    existing = _make_fake_conn("a")
    added = _make_fake_conn("b")
    mgr = MCPClientManager(config=_config(a=_server_def()))
    with _patch_connection_factory({"a": existing, "b": added}):
        await mgr.connect_all()
        await mgr.on_config_change(_config(a=_server_def(), b=_server_def()))
    added.connect.assert_awaited_once()
    assert mgr.get_connection("b") is added


async def test_on_config_change_removes_dropped_server():
    a = _make_fake_conn("a")
    b = _make_fake_conn("b")
    mgr = MCPClientManager(config=_config(a=_server_def(), b=_server_def()))
    with _patch_connection_factory({"a": a, "b": b}):
        await mgr.connect_all()
        await mgr.on_config_change(_config(a=_server_def()))
    b.disconnect.assert_awaited_once()
    assert mgr.get_connection("b") is None
    assert mgr.get_connection("a") is a


async def test_on_config_change_reconnects_when_command_changes():
    old = _make_fake_conn("a")
    new = _make_fake_conn("a")
    # factory must return old first time, new second time
    seq = [old, new]
    mgr = MCPClientManager(config=_config(a=_server_def(command="old-cmd")))
    with patch("ploston_core.mcp.manager.MCPConnection", side_effect=lambda *a, **k: seq.pop(0)):
        await mgr.connect_all()
        await mgr.on_config_change(_config(a=_server_def(command="new-cmd")))
    old.disconnect.assert_awaited_once()
    new.connect.assert_awaited_once()
    assert mgr.get_connection("a") is new


async def test_on_config_change_reconnects_when_env_changes():
    old = _make_fake_conn("a")
    new = _make_fake_conn("a")
    seq = [old, new]
    mgr = MCPClientManager(config=_config(a=_server_def(command="cmd", env={"X": "1"})))
    with patch("ploston_core.mcp.manager.MCPConnection", side_effect=lambda *a, **k: seq.pop(0)):
        await mgr.connect_all()
        await mgr.on_config_change(_config(a=_server_def(command="cmd", env={"X": "2"})))
    old.disconnect.assert_awaited_once()
    new.connect.assert_awaited_once()


async def test_on_config_change_no_change_keeps_same_connection():
    a = _make_fake_conn("a")
    mgr = MCPClientManager(config=_config(a=_server_def(command="cmd", env={"X": "1"})))
    with patch("ploston_core.mcp.manager.MCPConnection", return_value=a):
        await mgr.connect_all()
        a.disconnect.reset_mock()
        a.connect.reset_mock()
        await mgr.on_config_change(_config(a=_server_def(command="cmd", env={"X": "1"})))
    # unchanged server: no disconnect/reconnect
    a.disconnect.assert_not_awaited()
    a.connect.assert_not_awaited()
    assert mgr.get_connection("a") is a


async def test_on_config_change_updates_stored_config():
    a = _make_fake_conn("a")
    mgr = MCPClientManager(config=_config(a=_server_def(command="cmd")))
    new_cfg = _config(a=_server_def(command="cmd"), b=_make_b())
    b = _make_fake_conn("b")
    with patch("ploston_core.mcp.manager.MCPConnection", side_effect=[a, b]):
        await mgr.connect_all()
        await mgr.on_config_change(new_cfg)
    assert mgr._config is new_cfg


def _make_b() -> MCPServerDefinition:
    return _server_def(command="cmd-b")
