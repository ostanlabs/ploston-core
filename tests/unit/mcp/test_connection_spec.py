"""Spec tests for MCPConnection (ploston_core.mcp.connection).

The unit under test is MCPConnection. The mocked boundary is the FastMCP
``Client`` (the transport / process / network layer). We patch
``ploston_core.mcp.connection.Client`` with a fake whose ``list_tools`` and
``call_tool`` we control, and let MCPConnection drive its own state machine,
content extraction, retry logic and error contracts.

Contracts asserted (per docstrings):
- connect: retries with backoff, raises AELError(TOOL_UNAVAILABLE) on exhaustion,
  is a no-op when already connected, sets status appropriately.
- refresh_tools: requires connected/connecting + initialized client else
  TOOL_UNAVAILABLE; parses tool schemas; propagates list_tools errors.
- call_tool: TOOL_UNAVAILABLE when not connected / client missing,
  TOOL_REJECTED when tool unknown, TOOL_TIMEOUT on TimeoutError, success path
  produces MCPCallResult with parsed content + isError handling.
- disconnect: no-op when already disconnected, closes exit stack, clears tools,
  tolerates close errors / timeout.
- _get_transport_source / _create_stdio_transport: transport selection + the
  config-validation error contracts.
- content extraction helpers: TextContent, EmbeddedResource (text + binary),
  ResourceLink, ImageContent, dicts, bare strings, JSON parsing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.config.models import MCPServerDefinition
from ploston_core.errors.errors import AELError
from ploston_core.mcp.connection import MCPConnection
from ploston_core.types import ConnectionStatus, MCPTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stdio_cfg(command: str = "echo hi", **kw) -> MCPServerDefinition:
    return MCPServerDefinition(command=command, transport=MCPTransport.STDIO, **kw)


def _http_cfg(url: str = "http://localhost:9999/mcp", **kw) -> MCPServerDefinition:
    return MCPServerDefinition(url=url, transport=MCPTransport.HTTP, **kw)


def _fake_tool(name: str, description: str = "desc", input_schema: dict | None = None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=input_schema if input_schema is not None else {"type": "object"},
    )


def _install_fake_client(tools=None, call_result=None, call_side_effect=None):
    """Return a fake FastMCP Client supporting async context + list/call tools."""
    client = MagicMock(name="FakeClient")
    client.list_tools = AsyncMock(return_value=tools or [])
    if call_side_effect is not None:
        client.call_tool = AsyncMock(side_effect=call_side_effect)
    else:
        client.call_tool = AsyncMock(return_value=call_result)
    return client


async def _connected_conn(name="srv", tools=None, **client_kw):
    """Build a connected MCPConnection backed by a fake Client."""
    conn = MCPConnection(name=name, config=_http_cfg())
    client = _install_fake_client(tools=tools, **client_kw)
    conn._get_transport_source = lambda: "http://x/mcp"  # type: ignore
    with patch("ploston_core.mcp.connection.Client", return_value=client):
        await conn.connect()
    return conn, client


# ---------------------------------------------------------------------------
# connect — success / no-op / retry / failure
# ---------------------------------------------------------------------------


async def test_connect_success_sets_connected_and_fetches_tools():
    conn, client = await _connected_conn(tools=[_fake_tool("t1")])
    assert conn.status == ConnectionStatus.CONNECTED
    assert [t.name for t in conn.list_tools()] == ["t1"]
    client.list_tools.assert_awaited()
    assert conn.get_status().last_error is None


async def test_connect_noop_when_already_connected():
    conn, client = await _connected_conn(tools=[_fake_tool("t1")])
    client.list_tools.reset_mock()
    # Calling connect again must short-circuit (already connected)
    with patch("ploston_core.mcp.connection.Client") as ctor:
        await conn.connect()
        ctor.assert_not_called()
    client.list_tools.assert_not_awaited()


async def test_connect_failure_raises_tool_unavailable_and_sets_error():
    conn = MCPConnection(name="srv", config=_http_cfg())
    conn._get_transport_source = lambda: "http://x/mcp"  # type: ignore
    failing = MagicMock()
    failing.list_tools = AsyncMock(side_effect=RuntimeError("handshake failed"))
    with patch("ploston_core.mcp.connection.Client", return_value=failing):
        with pytest.raises(AELError) as ei:
            await conn.connect(max_retries=0)
    assert ei.value.code == "TOOL_UNAVAILABLE"
    assert conn.status == ConnectionStatus.ERROR
    assert conn.get_status().last_error is not None


async def test_connect_retries_then_succeeds():
    """connect must retry up to max_retries and succeed on a later attempt."""
    conn = MCPConnection(name="srv", config=_http_cfg())
    conn._get_transport_source = lambda: "http://x/mcp"  # type: ignore

    good = _install_fake_client(tools=[_fake_tool("ok")])
    calls = {"n": 0}

    def _ctor(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            bad = MagicMock()
            bad.list_tools = AsyncMock(side_effect=RuntimeError("transient"))
            return bad
        return good

    with patch("ploston_core.mcp.connection.Client", side_effect=_ctor):
        with patch("ploston_core.mcp.connection.asyncio.sleep", new=AsyncMock()):
            await conn.connect(max_retries=2, initial_delay=0.01, max_delay=0.02)

    assert conn.status == ConnectionStatus.CONNECTED
    assert calls["n"] == 2  # one failure + one success


async def test_connect_exhausts_retries_then_raises():
    conn = MCPConnection(name="srv", config=_http_cfg())
    conn._get_transport_source = lambda: "http://x/mcp"  # type: ignore
    bad = MagicMock()
    bad.list_tools = AsyncMock(side_effect=RuntimeError("always fails"))
    with patch("ploston_core.mcp.connection.Client", return_value=bad):
        with patch("ploston_core.mcp.connection.asyncio.sleep", new=AsyncMock()) as slept:
            with pytest.raises(AELError) as ei:
                await conn.connect(max_retries=2, initial_delay=0.01, max_delay=0.02)
    assert ei.value.code == "TOOL_UNAVAILABLE"
    # slept between the 3 attempts -> 2 sleeps
    assert slept.await_count == 2
    assert conn.status == ConnectionStatus.ERROR


# ---------------------------------------------------------------------------
# refresh_tools
# ---------------------------------------------------------------------------


async def test_refresh_tools_when_disconnected_raises_tool_unavailable():
    conn = MCPConnection(name="srv", config=_http_cfg())
    assert conn.status == ConnectionStatus.DISCONNECTED
    with pytest.raises(AELError) as ei:
        await conn.refresh_tools()
    assert ei.value.code == "TOOL_UNAVAILABLE"


async def test_refresh_tools_parses_schema_fields():
    conn, client = await _connected_conn(
        tools=[_fake_tool("alpha", "the alpha", {"type": "object", "x": 1})]
    )
    client.list_tools = AsyncMock(return_value=[_fake_tool("beta", "the beta", {"type": "object"})])
    tools = await conn.refresh_tools()
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "beta"
    assert t.description == "the beta"
    assert t.input_schema == {"type": "object"}
    assert t.output_schema is None


async def test_refresh_tools_handles_none_description():
    conn, client = await _connected_conn(tools=[])
    client.list_tools = AsyncMock(return_value=[_fake_tool("nodesc", description=None)])
    tools = await conn.refresh_tools()
    assert tools[0].description == ""  # None -> "" per contract


async def test_refresh_tools_propagates_list_tools_error():
    conn, client = await _connected_conn(tools=[])
    client.list_tools = AsyncMock(side_effect=RuntimeError("rpc error"))
    with pytest.raises(RuntimeError):
        await conn.refresh_tools()


async def test_refresh_tools_replaces_existing_tools():
    conn, client = await _connected_conn(tools=[_fake_tool("old1"), _fake_tool("old2")])
    assert {t.name for t in conn.list_tools()} == {"old1", "old2"}
    client.list_tools = AsyncMock(return_value=[_fake_tool("new1")])
    await conn.refresh_tools()
    assert {t.name for t in conn.list_tools()} == {"new1"}


# ---------------------------------------------------------------------------
# call_tool
# ---------------------------------------------------------------------------


async def test_call_tool_not_connected_raises_tool_unavailable():
    conn = MCPConnection(name="srv", config=_http_cfg())
    with pytest.raises(AELError) as ei:
        await conn.call_tool("anything", {})
    assert ei.value.code == "TOOL_UNAVAILABLE"
    assert ei.value.tool_name == "anything"


async def test_call_tool_unknown_tool_raises_tool_rejected():
    conn, _ = await _connected_conn(tools=[_fake_tool("known")])
    with pytest.raises(AELError) as ei:
        await conn.call_tool("ghost", {})
    assert ei.value.code == "TOOL_REJECTED"
    assert ei.value.tool_name == "ghost"


async def test_call_tool_success_returns_parsed_result():
    result_obj = SimpleNamespace(
        content=[SimpleNamespace(text='{"answer": 42}')],
        isError=False,
        structuredContent={"answer": 42},
    )
    conn, client = await _connected_conn(tools=[_fake_tool("calc")], call_result=result_obj)
    res = await conn.call_tool("calc", {"q": 1})
    assert res.success is True
    assert res.is_error is False
    assert res.content == {"answer": 42}  # JSON parsed
    assert res.structured_content == {"answer": 42}
    assert res.duration_ms >= 0
    client.call_tool.assert_awaited_once_with("calc", {"q": 1})


async def test_call_tool_error_flag_marks_failure():
    """When result.isError is True, success=False and error is populated."""
    result_obj = SimpleNamespace(
        content=[SimpleNamespace(text="boom happened")],
        isError=True,
    )
    conn, _ = await _connected_conn(tools=[_fake_tool("op")], call_result=result_obj)
    res = await conn.call_tool("op", {})
    assert res.success is False
    assert res.is_error is True
    assert res.error == "boom happened"


async def test_call_tool_timeout_raises_tool_timeout():
    conn, _ = await _connected_conn(
        tools=[_fake_tool("slow")], call_side_effect=TimeoutError("timed out")
    )
    with pytest.raises(AELError) as ei:
        await conn.call_tool("slow", {})
    assert ei.value.code == "TOOL_TIMEOUT"
    assert ei.value.tool_name == "slow"


async def test_call_tool_generic_error_propagates():
    conn, _ = await _connected_conn(
        tools=[_fake_tool("op")], call_side_effect=ValueError("malformed response")
    )
    with pytest.raises(ValueError):
        await conn.call_tool("op", {})


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


async def test_disconnect_noop_when_already_disconnected():
    conn = MCPConnection(name="srv", config=_http_cfg())
    # Should not raise and should not need a client
    await conn.disconnect()
    assert conn.status == ConnectionStatus.DISCONNECTED


async def test_disconnect_clears_state_and_tools():
    conn, _ = await _connected_conn(tools=[_fake_tool("t1")])
    assert conn.status == ConnectionStatus.CONNECTED
    await conn.disconnect()
    assert conn.status == ConnectionStatus.DISCONNECTED
    assert conn.list_tools() == []
    assert conn._client is None


async def test_disconnect_tolerates_close_error():
    conn, _ = await _connected_conn(tools=[_fake_tool("t1")])
    # Make the exit stack raise on close — disconnect must still complete.
    conn._exit_stack = MagicMock()
    conn._exit_stack.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
    await conn.disconnect()
    assert conn.status == ConnectionStatus.DISCONNECTED


async def test_disconnect_handles_timeout():
    conn, _ = await _connected_conn(tools=[_fake_tool("t1")])

    async def _hang():
        await asyncio.sleep(10)

    conn._exit_stack = MagicMock()
    conn._exit_stack.aclose = AsyncMock(side_effect=_hang)
    await conn.disconnect(timeout=0.05)
    assert conn.status == ConnectionStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# _get_transport_source — transport selection + validation contracts
# ---------------------------------------------------------------------------


def test_transport_source_stdio_missing_command_raises():
    conn = MCPConnection(name="srv", config=MCPServerDefinition(transport=MCPTransport.STDIO))
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


def test_transport_source_stdio_invalid_shlex_raises():
    conn = MCPConnection(name="srv", config=_stdio_cfg(command='echo "unterminated'))
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


def test_transport_source_http_missing_url_raises():
    conn = MCPConnection(name="srv", config=MCPServerDefinition(transport=MCPTransport.HTTP))
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


def test_transport_source_http_appends_mcp_suffix():
    from fastmcp.client.transports import StreamableHttpTransport

    conn = MCPConnection(name="srv", config=_http_cfg(url="http://host:8080"))
    t = conn._get_transport_source()
    assert isinstance(t, StreamableHttpTransport)


def test_transport_source_http_sse_endpoint_uses_sse_transport():
    from fastmcp.client.transports import SSETransport

    conn = MCPConnection(name="srv", config=_http_cfg(url="http://host:8080/sse"))
    t = conn._get_transport_source()
    assert isinstance(t, SSETransport)


def test_transport_source_unsupported_transport_raises():
    cfg = _stdio_cfg()
    # Force an unsupported transport value the branch logic doesn't recognise.
    cfg.transport = "carrier-pigeon"  # type: ignore
    conn = MCPConnection(name="srv", config=cfg)
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


# ---------------------------------------------------------------------------
# _create_stdio_transport — per-command selection + validation
# ---------------------------------------------------------------------------


def test_create_stdio_npx_transport():
    from fastmcp.client.transports import NpxStdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="npx -y some-pkg --flag"))
    t = conn._get_transport_source()
    assert isinstance(t, NpxStdioTransport)


def test_create_stdio_npx_no_package_raises():
    conn = MCPConnection(name="srv", config=_stdio_cfg(command="npx -y"))
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


def test_create_stdio_uvx_transport():
    from fastmcp.client.transports import UvxStdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="uvx some-tool arg1"))
    t = conn._get_transport_source()
    assert isinstance(t, UvxStdioTransport)


def test_create_stdio_uvx_no_tool_raises():
    conn = MCPConnection(name="srv", config=_stdio_cfg(command="uvx"))
    with pytest.raises(AELError) as ei:
        conn._get_transport_source()
    assert ei.value.code == "TOOL_UNAVAILABLE"


def test_create_stdio_python_transport():
    from fastmcp.client.transports import StdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="python server.py"))
    t = conn._get_transport_source()
    assert isinstance(t, StdioTransport)


def test_create_stdio_node_transport():
    from fastmcp.client.transports import StdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="node server.js"))
    assert isinstance(conn._get_transport_source(), StdioTransport)


def test_create_stdio_generic_command_transport():
    from fastmcp.client.transports import StdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="my-custom-bin --foo"))
    assert isinstance(conn._get_transport_source(), StdioTransport)


def test_create_stdio_npx_with_package_flag():
    """npx -p <pkg> form: -p consumes the next token (the package name)."""
    from fastmcp.client.transports import NpxStdioTransport

    conn = MCPConnection(name="srv", config=_stdio_cfg(command="npx -p left-pad real-pkg arg"))
    t = conn._get_transport_source()
    assert isinstance(t, NpxStdioTransport)


# ---------------------------------------------------------------------------
# content extraction helpers
# ---------------------------------------------------------------------------


def test_extract_text_from_text_content():
    item = SimpleNamespace(text="hello")
    assert MCPConnection._extract_text_from_item(item) == "hello"


def test_extract_text_from_embedded_resource_text():
    resource = SimpleNamespace(text="file contents", blob=None)
    item = SimpleNamespace(resource=resource)
    assert MCPConnection._extract_text_from_item(item) == "file contents"


def test_extract_text_from_embedded_resource_binary():
    resource = SimpleNamespace(text=None, blob=b"\x00\x01\x02", mimeType="application/pdf")
    item = SimpleNamespace(resource=resource)
    out = MCPConnection._extract_text_from_item(item)
    assert "binary content" in out
    assert "application/pdf" in out


def test_extract_text_from_resource_link():
    item = SimpleNamespace(uri="https://x/y", name="thing", mimeType="text/plain")
    out = MCPConnection._extract_text_from_item(item)
    assert "resource link" in out
    assert "https://x/y" in out


def test_extract_text_from_image_content():
    item = SimpleNamespace(data="base64data", mimeType="image/png")
    out = MCPConnection._extract_text_from_item(item)
    assert "image content" in out
    assert "image/png" in out


def test_extract_text_from_legacy_dict():
    assert MCPConnection._extract_text_from_item({"type": "text", "text": "legacy"}) == "legacy"


def test_extract_text_from_bare_string():
    assert MCPConnection._extract_text_from_item("plain") == "plain"


def test_extract_text_from_unrecognised_returns_none():
    assert MCPConnection._extract_text_from_item(object()) is None


def test_extract_fastmcp_content_empty_result_returns_empty_string():
    conn = MCPConnection(name="srv", config=_http_cfg())
    assert conn._extract_fastmcp_content(None) == ""


def test_extract_fastmcp_content_joins_multiple_items():
    conn = MCPConnection(name="srv", config=_http_cfg())
    result = SimpleNamespace(content=[SimpleNamespace(text="line1"), SimpleNamespace(text="line2")])
    assert conn._extract_fastmcp_content(result) == "line1\nline2"


def test_extract_fastmcp_content_parses_json():
    conn = MCPConnection(name="srv", config=_http_cfg())
    result = SimpleNamespace(content=[SimpleNamespace(text='{"k": "v"}')])
    assert conn._extract_fastmcp_content(result) == {"k": "v"}


def test_extract_fastmcp_content_non_json_text_returns_string():
    conn = MCPConnection(name="srv", config=_http_cfg())
    result = SimpleNamespace(content=[SimpleNamespace(text="just text")])
    assert conn._extract_fastmcp_content(result) == "just text"


def test_extract_fastmcp_content_legacy_list():
    conn = MCPConnection(name="srv", config=_http_cfg())
    result = [SimpleNamespace(text="a"), SimpleNamespace(text="b")]
    assert conn._extract_fastmcp_content(result) == "a\nb"


# ---------------------------------------------------------------------------
# status / accessors
# ---------------------------------------------------------------------------


def test_get_status_initial_disconnected():
    conn = MCPConnection(name="srv", config=_http_cfg())
    st = conn.get_status()
    assert st.name == "srv"
    assert st.status == ConnectionStatus.DISCONNECTED
    assert st.tools == []
    assert st.last_connected is None


def test_get_tool_returns_none_when_absent():
    conn = MCPConnection(name="srv", config=_http_cfg())
    assert conn.get_tool("nope") is None


def test_get_log_path_reflects_config():
    from pathlib import Path

    p = Path("/tmp/srv.log")
    conn = MCPConnection(name="srv", config=_http_cfg(), log_file=p)
    assert conn.get_log_path() == p
