"""Unit tests for the redesigned workflow authoring tool surface (DEC-185).

Covers:
- S-277: workflow_list_tools (LT-01..LT-05)
- S-278: workflow_call_tool (CT-01..CT-07)
- S-279: workflow_tool_schema batch mode (TS-B01..TS-B04)
- S-280: workflow_schema cleanup + not-found breadcrumb (CL-01..CL-06)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.errors import create_error
from ploston_core.invoker.types import ToolCallResult
from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus
from ploston_core.workflow.tools import (
    ALL_WORKFLOW_MGMT_TOOLS,
    WORKFLOW_CALL_TOOL_TOOL,
    WORKFLOW_LIST_TOOLS_TOOL,
    WORKFLOW_MGMT_TOOL_NAMES,
    WORKFLOW_TOOL_SCHEMA_TOOL,
    WorkflowToolsProvider,
)


def _parse(mcp_response: dict) -> dict:
    return json.loads(mcp_response["content"][0]["text"])


@pytest.fixture
def mock_workflow_registry():
    reg = MagicMock()
    reg.list_workflows.return_value = []
    return reg


@pytest.fixture
def mock_tool_registry():
    python_exec = ToolDefinition(
        name="python_exec",
        description="Execute Python code.",
        source=ToolSource.SYSTEM,
        server_name="system",
        input_schema={
            "type": "object",
            "required": ["code"],
            "properties": {"code": {"type": "string"}},
        },
        status=ToolStatus.AVAILABLE,
    )
    github_list = ToolDefinition(
        name="list_repos",
        description="List GitHub repositories.",
        source=ToolSource.MCP,
        server_name="github",
        input_schema={"type": "object", "properties": {"org": {"type": "string"}}},
        status=ToolStatus.AVAILABLE,
    )

    def list_tools_side_effect(server_name=None, **_kw):
        tools = [python_exec, github_list]
        if server_name is not None:
            return [t for t in tools if t.server_name == server_name]
        return tools

    reg = MagicMock()
    reg.list_tools.side_effect = list_tools_side_effect
    return reg


@pytest.fixture
def mock_runner_registry():
    runner = SimpleNamespace(
        name="mac",
        available_tools=[
            {
                "name": "fs__read_file",
                "description": "Read a file",
                "inputSchema": {"type": "object", "required": ["path"]},
            },
            "docker__run_container",
        ],
    )
    reg = MagicMock()
    reg.list.return_value = [runner]
    return reg


@pytest.fixture
def provider(mock_workflow_registry, mock_tool_registry, mock_runner_registry):
    return WorkflowToolsProvider(
        workflow_registry=mock_workflow_registry,
        tool_registry=mock_tool_registry,
        runner_registry=mock_runner_registry,
    )


# ── S-277: workflow_list_tools ─────────────────────────────────────


class TestListTools:
    @pytest.mark.asyncio
    async def test_lt01_exposes_all_servers(self, provider):
        """LT-01: workflow_list_tools without filter returns every mcp server."""
        raw = await provider.call("workflow_list_tools", {})
        result = _parse(raw)
        servers = {g["mcp_server"] for g in result["tools"]}
        assert {"system", "github", "fs", "docker"}.issubset(servers)

    @pytest.mark.asyncio
    async def test_lt02_filters_by_single_server(self, provider):
        """LT-02: mcp_servers filter narrows the response to a single server."""
        raw = await provider.call("workflow_list_tools", {"mcp_servers": ["system"]})
        result = _parse(raw)
        servers = {g["mcp_server"] for g in result["tools"]}
        assert servers == {"system"}

    @pytest.mark.asyncio
    async def test_lt03_filters_by_multiple_servers(self, provider):
        """LT-03: mcp_servers accepts multiple values."""
        raw = await provider.call("workflow_list_tools", {"mcp_servers": ["github", "fs"]})
        result = _parse(raw)
        servers = {g["mcp_server"] for g in result["tools"]}
        assert servers == {"github", "fs"}

    @pytest.mark.asyncio
    async def test_lt04_unknown_server_returns_empty(self, provider):
        """LT-04: Unknown mcp_server filter returns empty tools list."""
        raw = await provider.call("workflow_list_tools", {"mcp_servers": ["nowhere"]})
        result = _parse(raw)
        assert result == {"tools": []}

    @pytest.mark.asyncio
    async def test_lt05_group_shape(self, provider):
        """LT-05: Each group carries mcp_server, runner, and sorted tools list."""
        raw = await provider.call("workflow_list_tools", {"mcp_servers": ["fs"]})
        result = _parse(raw)
        assert len(result["tools"]) == 1
        group = result["tools"][0]
        assert group["mcp_server"] == "fs"
        assert group["runner"] == "mac"
        assert group["tools"] == sorted(group["tools"])

    def test_list_tools_registered_in_mgmt_set(self):
        """WORKFLOW_MGMT_TOOL_NAMES and ALL_WORKFLOW_MGMT_TOOLS include the new tool."""
        assert "workflow_list_tools" in WORKFLOW_MGMT_TOOL_NAMES
        names = [t["name"] for t in ALL_WORKFLOW_MGMT_TOOLS]
        assert "workflow_list_tools" in names
        assert WORKFLOW_LIST_TOOLS_TOOL["name"] == "workflow_list_tools"


# ── S-278: workflow_call_tool ──────────────────────────────────────


def _make_invoker(
    success: bool = True,
    output=None,
    error: Exception | None = None,
    raises: Exception | None = None,
    duration_ms: int = 12,
):
    invoker = MagicMock()
    if raises is not None:
        invoker.invoke = AsyncMock(side_effect=raises)
    else:
        invoker.invoke = AsyncMock(
            return_value=ToolCallResult(
                success=success,
                output=output,
                duration_ms=duration_ms,
                tool_name="x",
                error=error,
            )
        )
    return invoker


class TestCallTool:
    @pytest.mark.asyncio
    async def test_ct01_cp_direct_invocation(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-01: CP-direct tools invoke by bare name and normalize output."""
        invoker = _make_invoker(output=[{"type": "text", "text": '{"stdout": "hi"}'}])
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call(
            "workflow_call_tool",
            {"mcp": "system", "tool": "python_exec", "params": {"code": "print(1)"}},
        )
        result = _parse(raw)
        assert result["success"] is True
        assert result["source"] == "cp"
        assert result["runner"] is None
        assert result["output"] == {"stdout": "hi"}  # normalized from text envelope
        invoker.invoke.assert_awaited_once_with("python_exec", {"code": "print(1)"})

    @pytest.mark.asyncio
    async def test_ct02_runner_invocation(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-02: Runner-hosted tools invoke via runner__mcp__tool triple name."""
        invoker = _make_invoker(output={"result": "ok"})
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call(
            "workflow_call_tool",
            {"mcp": "fs", "tool": "read_file", "params": {"path": "/tmp/a"}},
        )
        result = _parse(raw)
        assert result["success"] is True
        assert result["source"] == "runner"
        assert result["runner"] == "mac"
        invoker.invoke.assert_awaited_once_with("mac__fs__read_file", {"path": "/tmp/a"})

    @pytest.mark.asyncio
    async def test_ct03_unknown_tool_returns_hint(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-03: Unknown tool returns structured not-found with workflow_list_tools hint."""
        invoker = _make_invoker()
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call(
            "workflow_call_tool", {"mcp": "nope", "tool": "ghost", "params": {}}
        )
        result = _parse(raw)
        assert result["success"] is False
        assert "error" in result
        assert "workflow_list_tools" in result["hint"]
        invoker.invoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ct04_invoker_raises_aelerror(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-04: AELError from invoker becomes structured {success:false,...}."""
        invoker = _make_invoker(raises=create_error("TOOL_TIMEOUT", tool_name="python_exec"))
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call(
            "workflow_call_tool", {"mcp": "system", "tool": "python_exec", "params": {"code": "x"}}
        )
        result = _parse(raw)
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_ct05_invoker_returns_failure(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-05: ToolCallResult(success=False, ...) is surfaced as structured error."""
        invoker = _make_invoker(success=False, error=RuntimeError("boom"))
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call(
            "workflow_call_tool", {"mcp": "system", "tool": "python_exec", "params": {}}
        )
        result = _parse(raw)
        assert result["success"] is False
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_ct06_missing_invoker_raises(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-06: Missing ToolInvoker surfaces INTERNAL error to the caller."""
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
        )
        with pytest.raises(Exception):
            await provider.call(
                "workflow_call_tool", {"mcp": "system", "tool": "python_exec", "params": {}}
            )

    @pytest.mark.asyncio
    async def test_ct07_defaults_empty_params(
        self, mock_workflow_registry, mock_tool_registry, mock_runner_registry
    ):
        """CT-07: Missing 'params' defaults to an empty dict."""
        invoker = _make_invoker(output={"ok": True})
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=mock_tool_registry,
            runner_registry=mock_runner_registry,
            tool_invoker=invoker,
        )
        raw = await provider.call("workflow_call_tool", {"mcp": "system", "tool": "python_exec"})
        result = _parse(raw)
        assert result["success"] is True
        invoker.invoke.assert_awaited_once_with("python_exec", {})

    def test_call_tool_registered_in_mgmt_set(self):
        """workflow_call_tool appears in registration sets and definition."""
        assert "workflow_call_tool" in WORKFLOW_MGMT_TOOL_NAMES
        names = [t["name"] for t in ALL_WORKFLOW_MGMT_TOOLS]
        assert "workflow_call_tool" in names
        assert WORKFLOW_CALL_TOOL_TOOL["name"] == "workflow_call_tool"


# ── S-279: workflow_tool_schema batch mode ─────────────────────────


class TestToolSchemaBatch:
    @pytest.mark.asyncio
    async def test_tsb01_batch_single_entry(self, provider):
        """TS-B01: tools=[{mcp, tool}] returns results list of length 1."""
        raw = await provider.call(
            "workflow_tool_schema",
            {"tools": [{"mcp": "system", "tool": "python_exec"}]},
        )
        result = _parse(raw)
        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["source"] == "cp"

    @pytest.mark.asyncio
    async def test_tsb02_batch_preserves_order(self, provider):
        """TS-B02: Results are returned in request order, including misses."""
        raw = await provider.call(
            "workflow_tool_schema",
            {
                "tools": [
                    {"mcp": "system", "tool": "python_exec"},
                    {"mcp": "nope", "tool": "ghost"},
                    {"mcp": "fs", "tool": "read_file"},
                ]
            },
        )
        result = _parse(raw)
        assert [r.get("source", "not_found") for r in result["results"]] == [
            "cp",
            "not_found",
            "runner",
        ]

    @pytest.mark.asyncio
    async def test_tsb03_empty_batch_is_noop(self, provider):
        """TS-B03: tools=[] is treated as a valid empty request."""
        raw = await provider.call("workflow_tool_schema", {"tools": []})
        result = _parse(raw)
        assert result == {"results": []}

    @pytest.mark.asyncio
    async def test_tsb04_batch_validates_entries(self, provider):
        """TS-B04: Entries missing mcp or tool raise a structured error."""
        with pytest.raises(Exception):
            await provider.call("workflow_tool_schema", {"tools": [{"mcp": "system"}]})

    def test_tool_schema_inputschema_includes_tools(self):
        """workflow_tool_schema's MCP inputSchema advertises the 'tools' batch field."""
        props = WORKFLOW_TOOL_SCHEMA_TOOL["inputSchema"]["properties"]
        assert "tools" in props
        assert props["tools"]["type"] == "array"


# ── S-280: workflow_schema cleanup + breadcrumbs ───────────────────


class TestSchemaCleanup:
    @pytest.mark.asyncio
    async def test_cl01_full_schema_drops_available_tools(self, provider):
        """CL-01: workflow_schema full response no longer embeds available_tools."""
        raw = await provider.call("workflow_schema", {})
        result = _parse(raw)
        assert "available_tools" not in result
        assert "sections" in result
        assert "schema" in result

    @pytest.mark.asyncio
    async def test_cl02_section_mode_untouched(self, provider):
        """CL-02: section-mode path is untouched by the cleanup (Clar-1)."""
        raw = await provider.call("workflow_schema", {"section": "steps"})
        result = _parse(raw)
        assert result["section"] == "steps"
        assert "schema" in result

    @pytest.mark.asyncio
    async def test_cl03_not_found_replaces_available_tools_with_hint(self, provider):
        """CL-03: Not-found _resolve_tool_schema returns hint, not available_tools."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "nowhere", "tool": "ghost"})
        result = _parse(raw)
        assert result.get("found") is False
        assert "available_tools" not in result
        assert "workflow_list_tools" in result["hint"]

    def test_cl04_schema_tool_description_mentions_list_tools(self):
        """CL-04: WORKFLOW_SCHEMA_TOOL points to workflow_list_tools for discovery."""
        from ploston_core.workflow.tools import WORKFLOW_SCHEMA_TOOL

        assert "workflow_list_tools" in WORKFLOW_SCHEMA_TOOL["description"]
        assert "available_tools" not in WORKFLOW_SCHEMA_TOOL["description"]

    def test_cl05_tool_schema_description_advertises_breadcrumbs(self):
        """CL-05: WORKFLOW_TOOL_SCHEMA_TOOL description references the new flow."""
        desc = WORKFLOW_TOOL_SCHEMA_TOOL["description"]
        assert "workflow_list_tools" in desc
        assert "workflow_call_tool" in desc

    def test_cl06_schema_generator_note_updated(self):
        """CL-06: schema_generator authoring note no longer references available_tools."""
        from ploston_core.workflow.schema_generator import generate_workflow_schema

        schema = generate_workflow_schema()
        serialized = json.dumps(schema)
        assert "workflow_list_tools" in serialized
        # The stale 'updated available_tools list' phrase must be gone.
        assert "updated available_tools" not in serialized


class TestOutputSchemaSpecCompliance:
    """MCP-spec compliance for ``outputSchema`` roots on management tools.

    The MCP tools spec requires every tool's ``outputSchema`` root to be
    ``type: "object"``. Strict clients (Claude Desktop's MCP SDK) discard
    the entire ``tools/list`` response when any tool violates this — which
    presents to the user as 'server connected but exposes no tools'.
    """

    def test_every_mgmt_tool_outputschema_root_is_object(self):
        for tool in ALL_WORKFLOW_MGMT_TOOLS:
            out = tool.get("outputSchema")
            if out is None:
                continue
            assert out.get("type") == "object", (
                f"{tool['name']}.outputSchema root must be type='object' "
                f"(MCP spec); got {out.get('type')!r} with top-level "
                f"keys={sorted(out.keys())}"
            )


class TestStructuredContentSpecCompliance:
    """MCP-spec compliance for ``structuredContent`` on management tools.

    Per MCP 2025-06-18 §tools/structured-content: when a tool's definition
    declares ``outputSchema``, every successful ``tools/call`` response
    must include ``structuredContent``. Strict clients (Claude Desktop's
    MCP SDK) raise
    ``-32600 Tool ... has an output schema but did not return structured
    content`` otherwise. Until 2026-04-25 every management tool returned
    only ``content`` text, which broke all schema-declaring read tools.
    """

    @pytest.mark.asyncio
    async def test_workflow_schema_response_carries_structured_content(self):
        provider = WorkflowToolsProvider(workflow_registry=MagicMock())
        resp = await provider.call("workflow_schema", {})
        assert "structuredContent" in resp, (
            "workflow_schema declares outputSchema; the MCP spec requires "
            "structuredContent on the response."
        )
        # The structured content must mirror the JSON-text content so the
        # two surfaces can't drift.
        assert resp["structuredContent"] == json.loads(resp["content"][0]["text"])

    @pytest.mark.asyncio
    async def test_workflow_list_response_carries_structured_content(self):
        registry = MagicMock()
        registry.list.return_value = []
        provider = WorkflowToolsProvider(workflow_registry=registry)
        resp = await provider.call("workflow_list", {})
        assert "structuredContent" in resp
        assert resp["structuredContent"] == json.loads(resp["content"][0]["text"])

    @pytest.mark.asyncio
    async def test_response_for_every_schema_declaring_mgmt_tool(self):
        # Defence-in-depth: any future mgmt tool added with an outputSchema
        # must populate structuredContent. Drive each handler with the
        # minimum viable arguments and assert the contract.
        registry = MagicMock()
        registry.list.return_value = []
        registry.get.return_value = None
        provider = WorkflowToolsProvider(workflow_registry=registry)

        # Map of tool -> (kwargs, allow_error). The handlers we can drive
        # without standing up tool/runner registries; the rest are covered
        # by their dedicated tests in this file and elsewhere.
        cases = [
            ("workflow_schema", {}),
            ("workflow_list", {}),
        ]
        for tool_name, args in cases:
            resp = await provider.call(tool_name, args)
            tool_def = next(t for t in ALL_WORKFLOW_MGMT_TOOLS if t["name"] == tool_name)
            if tool_def.get("outputSchema") is None:
                continue
            assert "structuredContent" in resp, (
                f"{tool_name} declares outputSchema but the response is "
                f"missing structuredContent — MCP -32600 regression."
            )
