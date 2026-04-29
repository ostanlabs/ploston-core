"""Unit tests for the redesigned workflow authoring tool surface (DEC-185).

Covers:
- S-277: workflow_list_tools (LT-01..LT-05)
- S-278: workflow_call_tool (CT-01..CT-07)
- S-279: workflow_tool_schema batch mode (TS-B01..TS-B04)
- S-280: workflow_schema cleanup + not-found breadcrumb (CL-01..CL-06)
- S-290: workflow_schema split into Tier 1 + on-demand sections (P2)
- S-291: workflow_create absorbs validation surface with draft_id (P3)
- S-292: workflow_patch ``set`` op + version semantics + live-safety (P4a)
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
from ploston_core.workflow.schema_generator import (
    AVAILABLE_SECTIONS,
    generate_section,
    generate_tier1_schema,
)
from ploston_core.workflow.tools import (
    ALL_WORKFLOW_MGMT_TOOLS,
    WORKFLOW_CALL_TOOL_TOOL,
    WORKFLOW_CREATE_TOOL,
    WORKFLOW_LIST_TOOLS_TOOL,
    WORKFLOW_MGMT_TOOL_NAMES,
    WORKFLOW_SCHEMA_TOOL,
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
        """CL-02: section-mode path returns the named section.

        Updated for S-290 P2 — the canonical section names are now
        sandbox_constraints, context_api, tool_steps, inputs, outputs,
        defaults, packages, examples. ``steps`` was renamed to ``tool_steps``.
        """
        raw = await provider.call("workflow_schema", {"section": "tool_steps"})
        result = _parse(raw)
        assert result["section"] == "tool_steps"
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


# ── S-290: workflow_schema split into Tier 1 + on-demand sections ──
# (P2 of the WORKFLOW_AUTHORING_DX_V2 spec.)


class TestSchemaSplitTier1:
    @pytest.mark.asyncio
    async def test_schema_no_section_returns_tier1(self, provider):
        """No-arg returns Tier 1 minimal schema, not the full dump."""
        raw = await provider.call("workflow_schema", {})
        result = _parse(raw)
        assert result["tier"] == 1
        assert "schema" in result
        assert "fields" in result["schema"]
        assert "step_types" in result["schema"]
        # Old full-dump markers must be absent.
        assert "code_steps" not in result["schema"]
        assert "properties" not in result["schema"]
        assert list(result["sections"]) == list(AVAILABLE_SECTIONS)

    def test_tier1_under_2k_tokens(self):
        """Rendered Tier 1 must stay under the spec budget (~2K tokens)."""
        # Heuristic: 1 token ≈ 4 chars of UTF-8 prose. Cap at 8000 chars
        # to leave headroom under the 2K-token spec budget.
        rendered = json.dumps(generate_tier1_schema(), separators=(",", ":"))
        char_count = len(rendered)
        token_estimate = char_count // 4
        assert token_estimate < 2000, (
            f"Tier 1 schema is ~{token_estimate} tokens ({char_count} chars); spec budget is 2K."
        )


class TestSchemaSplitSections:
    @pytest.mark.asyncio
    async def test_schema_section_sandbox_constraints(self, provider):
        raw = await provider.call("workflow_schema", {"section": "sandbox_constraints"})
        result = _parse(raw)
        assert result["section"] == "sandbox_constraints"
        assert "allowed_imports" in result["schema"]

    @pytest.mark.asyncio
    async def test_schema_section_context_api(self, provider):
        raw = await provider.call("workflow_schema", {"section": "context_api"})
        result = _parse(raw)
        assert result["section"] == "context_api"
        assert "context_api" in result["schema"]

    @pytest.mark.asyncio
    async def test_schema_invalid_section_returns_available_sections(self, provider):
        raw = await provider.call("workflow_schema", {"section": "nope"})
        result = _parse(raw)
        assert "error" in result
        assert "Unknown section" in result["error"]
        assert list(result["available_sections"]) == list(AVAILABLE_SECTIONS)

    @pytest.mark.asyncio
    async def test_section_response_lists_available_sections(self, provider):
        """Every section response carries the available_sections breadcrumb."""
        raw = await provider.call("workflow_schema", {"section": "tool_steps"})
        result = _parse(raw)
        assert list(result["available_sections"]) == list(AVAILABLE_SECTIONS)

    def test_every_advertised_section_resolvable(self):
        """Every name in AVAILABLE_SECTIONS must be resolvable by generate_section."""
        for name in AVAILABLE_SECTIONS:
            section = generate_section(name)
            assert isinstance(section, dict) and section, (
                f"section {name!r} must return a non-empty dict"
            )

    def test_unknown_section_raises_keyerror(self):
        with pytest.raises(KeyError):
            generate_section("nope")


class TestSchemaSplitToolDescriptions:
    def test_workflow_create_description_embeds_tier1(self):
        """workflow_create tool description must contain the Tier 1 cheatsheet."""
        desc = WORKFLOW_CREATE_TOOL["description"]
        assert "YAML schema (Tier 1):" in desc
        assert "tool_step:" in desc
        assert "code_step:" in desc
        assert "Templates:" in desc
        assert "workflow_schema(section=" in desc

    def test_workflow_schema_description_lists_sections(self):
        desc = WORKFLOW_SCHEMA_TOOL["description"]
        for name in ("sandbox_constraints", "context_api", "tool_steps"):
            assert name in desc

    def test_section_param_enum_matches_available_sections(self):
        """The inputSchema enum must match the canonical section list."""
        enum = WORKFLOW_SCHEMA_TOOL["inputSchema"]["properties"]["section"]["enum"]
        assert list(enum) == list(AVAILABLE_SECTIONS)


# ── M-081 follow-up: discovery section ───────────────────────────────
# Adds a principle-only "discovery" section that fronts the authoring
# flow with investigation discipline (narrow filters over broad calls,
# schema-then-call, source-over-surface).


class TestDiscoverySection:
    def test_discovery_listed_first(self):
        """`discovery` must lead AVAILABLE_SECTIONS so the more_detail
        breadcrumb and inputSchema enum surface it before any other
        section."""
        assert AVAILABLE_SECTIONS[0] == "discovery"

    def test_discovery_section_resolves_with_principles(self):
        """Section content is principle-only and stable."""
        section = generate_section("discovery")
        assert "principles" in section
        principles = section["principles"]
        # Each principle is a non-empty string keyed by a stable name.
        for key in (
            "narrow_over_broad",
            "schema_then_call",
            "source_over_surface",
            "investigate_in_conversation",
            "minimal_step_count",
        ):
            assert key in principles, f"missing principle: {key}"
            assert isinstance(principles[key], str) and principles[key].strip()
        # Companion fields documented in the spec.
        assert "investigation_toolbox" in section
        assert "anti_patterns" in section and section["anti_patterns"]
        assert "next" in section and "workflow_create" in section["next"]

    def test_discovery_section_is_principle_only(self):
        """Section must not name concrete MCP servers/tools — those live
        behind workflow_list_tools / workflow_tool_schema."""
        import json as _json

        blob = _json.dumps(generate_section("discovery")).lower()
        for forbidden in ("github", "grafana", "loki", "prometheus", "tempo"):
            assert forbidden not in blob, (
                f"discovery section must stay MCP-agnostic; found {forbidden!r}"
            )

    @pytest.mark.asyncio
    async def test_schema_section_discovery_via_provider(self, provider):
        raw = await provider.call("workflow_schema", {"section": "discovery"})
        result = _parse(raw)
        assert result["section"] == "discovery"
        assert "principles" in result["schema"]
        assert list(result["available_sections"]) == list(AVAILABLE_SECTIONS)


class TestDiscoverySurfacing:
    def test_tier1_carries_before_you_start_pointer(self):
        """Tier 1 schema must include a `before_you_start` field that
        points at the discovery section."""
        t1 = generate_tier1_schema()
        assert "before_you_start" in t1
        bys = t1["before_you_start"]
        assert "discovery" in bys
        assert "workflow_schema" in bys

    def test_create_description_points_at_discovery(self):
        """workflow_create description must include the discovery
        pointer so agents that skip the schema step still see it."""
        desc = WORKFLOW_CREATE_TOOL["description"]
        assert 'workflow_schema(section="discovery")' in desc

    def test_schema_description_mentions_discovery(self):
        """workflow_schema description lists discovery in the section
        catalogue and in the recommended flow."""
        desc = WORKFLOW_SCHEMA_TOOL["description"]
        assert "discovery" in desc
        assert 'workflow_schema(section="discovery")' in desc

    def test_tier1_rendered_block_includes_before_you_start(self):
        """The compact block embedded in workflow_create.description
        must surface the before_you_start line."""
        from ploston_core.workflow.tools import _TIER1_DESCRIPTION_BLOCK

        assert "Before you start:" in _TIER1_DESCRIPTION_BLOCK
        assert "discovery" in _TIER1_DESCRIPTION_BLOCK


# ── S-291 (P3): workflow_create absorbs validation surface ──────────
# (P3 of the WORKFLOW_AUTHORING_DX_V2 spec.)


@pytest.fixture
def real_tool_registry():
    """ToolRegistry mock with an ``echo`` tool on ``system`` — exposed via
    the public surface used by both the WorkflowValidator (``list_tools``,
    ``get_tool``) and WorkflowToolsProvider._build_available_tools."""
    from ploston_core.registry.types import ToolDefinition
    from ploston_core.types import ToolSource, ToolStatus

    echo = ToolDefinition(
        name="echo",
        description="Echo a message back.",
        source=ToolSource.SYSTEM,
        server_name="system",
        input_schema={
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
        },
        status=ToolStatus.AVAILABLE,
    )
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()
    tr.get.return_value = None
    tr.list_tools.return_value = [echo]
    return tr


@pytest.fixture
def real_registry(tmp_path, real_tool_registry):
    """A real WorkflowRegistry backed by tmp_path."""
    from ploston_core.workflow.registry import WorkflowRegistry

    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    config.draft_ttl_seconds = 1800
    return WorkflowRegistry(real_tool_registry, config)


@pytest.fixture
def real_provider(real_registry, real_tool_registry):
    return WorkflowToolsProvider(
        workflow_registry=real_registry,
        tool_registry=real_tool_registry,
    )


_VALID_YAML = (
    "name: dx_p3_valid\n"
    'version: "1.0.0"\n'
    "description: A valid workflow used for S-291 P3 round-trip tests.\n"
    "steps:\n"
    "  - id: greet\n"
    "    tool: echo\n"
    "    mcp: system\n"
    "    params:\n"
    "      message: hi\n"
)

_INVALID_TOOL_YAML = (
    "name: dx_p3_bad_tool\n"
    'version: "1.0.0"\n'
    "description: Step references a tool that does not exist.\n"
    "steps:\n"
    "  - id: greet\n"
    "    tool: ehco\n"
    "    mcp: system\n"
    "    params:\n"
    "      message: hi\n"
)

_RETURN_IN_CODE_YAML = (
    "name: dx_p3_return\n"
    'version: "1.0.0"\n'
    "description: Code step that uses 'return' instead of 'result ='.\n"
    "steps:\n"
    "  - id: compute\n"
    "    code: |\n"
    "      x = 1 + 1\n"
    "      return {'x': x}\n"
)


class TestWorkflowCreateDraftFlow:
    """workflow_create now produces drafts on validation failure (S-291 P3)."""

    @pytest.mark.asyncio
    async def test_valid_workflow_returns_created(self, real_provider):
        raw = await real_provider.call("workflow_create", {"yaml_content": _VALID_YAML})
        result = _parse(raw)
        assert result["status"] == "created"
        assert result["name"] == "dx_p3_valid"
        # No draft is stashed on success — draft_id may be omitted or null.
        assert result.get("draft_id") in (None, "")
        # Validation block is included even on success.
        assert result["validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_dry_run_valid_no_side_effects(self, real_registry, real_provider):
        """dry_run=true validates without registering or creating a draft."""
        raw = await real_provider.call(
            "workflow_create", {"yaml_content": _VALID_YAML, "dry_run": True}
        )
        result = _parse(raw)
        assert result["validation"]["valid"] is True
        # Workflow must NOT be registered.
        assert real_registry.get("dx_p3_valid") is None
        # No draft should be created for valid+dry_run.
        assert result.get("draft_id") in (None, "")

    @pytest.mark.asyncio
    async def test_invalid_tool_returns_draft_with_id(self, real_registry, real_provider):
        raw = await real_provider.call("workflow_create", {"yaml_content": _INVALID_TOOL_YAML})
        result = _parse(raw)
        assert result["status"] == "draft"
        assert result["validation"]["valid"] is False
        assert isinstance(result.get("draft_id"), str) and result["draft_id"]
        # The workflow must NOT be registered.
        assert real_registry.get("dx_p3_bad_tool") is None
        # The draft must be retrievable via the store.
        entry = real_registry.draft_store.get(result["draft_id"])
        assert entry is not None
        assert entry.yaml_content == _INVALID_TOOL_YAML

    @pytest.mark.asyncio
    async def test_return_in_code_step_caught_statically(self, real_provider):
        raw = await real_provider.call("workflow_create", {"yaml_content": _RETURN_IN_CODE_YAML})
        result = _parse(raw)
        assert result["status"] == "draft"
        errors = result["validation"]["errors"]
        return_errors = [
            e for e in errors if "return" in e["message"].lower() or e.get("kind") == "code_return"
        ]
        assert return_errors, f"expected a return-in-code error; got {errors}"

    @pytest.mark.asyncio
    async def test_suggested_fix_for_unknown_tool(self, real_provider):
        raw = await real_provider.call("workflow_create", {"yaml_content": _INVALID_TOOL_YAML})
        result = _parse(raw)
        errors = result["validation"]["errors"]
        # The unknown-tool error must carry a fuzzy-matched suggested_fix.
        unknown_tool_errors = [e for e in errors if e["path"].endswith(".tool")]
        assert unknown_tool_errors, f"expected an unknown-tool error; got {errors}"
        fix = unknown_tool_errors[0].get("suggested_fix")
        assert fix and fix.get("op") == "set"
        assert fix.get("value") == "echo"


class TestWorkflowPatchDraftRoundtrip:
    """End-to-end: invalid YAML → draft_id → workflow_patch → success (S-291 P3)."""

    @pytest.mark.asyncio
    async def test_unknown_tool_roundtrip_via_draft(self, real_registry, real_provider):
        # 1. Submit invalid YAML, receive draft_id.
        raw = await real_provider.call("workflow_create", {"yaml_content": _INVALID_TOOL_YAML})
        created = _parse(raw)
        assert created["status"] == "draft"
        draft_id = created["draft_id"]
        assert draft_id

        # 2. Apply the suggested_fix via workflow_patch on the draft.
        unknown_tool_errors = [
            e for e in created["validation"]["errors"] if e["path"].endswith(".tool")
        ]
        fix = unknown_tool_errors[0]["suggested_fix"]

        patch_raw = await real_provider.call(
            "workflow_patch",
            {"draft_id": draft_id, "operations": [fix]},
        )
        patched = _parse(patch_raw)

        # 3. After applying the fix, the workflow should validate and register.
        assert patched["status"] == "patched", f"expected status='patched', got {patched}"
        assert real_registry.get("dx_p3_bad_tool") is not None


class TestDeprecatedToolsRemoved:
    """``workflow_validate`` and ``workflow_update`` are removed entirely.

    Their capabilities are absorbed by ``workflow_create(dry_run=true)``
    and ``workflow_patch`` respectively.
    """

    def test_workflow_validate_not_in_tool_catalog(self):
        from ploston_core.workflow.tools import (
            ALL_WORKFLOW_MGMT_TOOLS,
            WORKFLOW_MGMT_TOOL_NAMES,
        )

        names = {t["name"] for t in ALL_WORKFLOW_MGMT_TOOLS}
        assert "workflow_validate" not in names
        assert "workflow_validate" not in WORKFLOW_MGMT_TOOL_NAMES

    def test_workflow_update_not_in_tool_catalog(self):
        from ploston_core.workflow.tools import (
            ALL_WORKFLOW_MGMT_TOOLS,
            WORKFLOW_MGMT_TOOL_NAMES,
        )

        names = {t["name"] for t in ALL_WORKFLOW_MGMT_TOOLS}
        assert "workflow_update" not in names
        assert "workflow_update" not in WORKFLOW_MGMT_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_workflow_validate_dispatch_raises(self, real_provider):
        # Dispatcher must reject the unknown tool name rather than silently
        # routing to a stale handler.
        with pytest.raises(Exception):
            await real_provider.call("workflow_validate", {"yaml_content": "name: x"})

    @pytest.mark.asyncio
    async def test_workflow_update_dispatch_raises(self, real_provider):
        with pytest.raises(Exception):
            await real_provider.call("workflow_update", {"name": "x", "yaml_content": "name: x"})

    @pytest.mark.asyncio
    async def test_create_dry_run_returns_validation_envelope(self, real_registry, real_provider):
        # Equivalent of the old ``workflow_validate``: an invalid YAML
        # comes back with ``status='draft'`` and an ``errors`` list, and
        # nothing is registered.
        raw = await real_provider.call(
            "workflow_create", {"yaml_content": _INVALID_TOOL_YAML, "dry_run": True}
        )
        result = _parse(raw)
        assert result["status"] == "draft"
        assert result["validation"]["valid"] is False
        assert isinstance(result["validation"]["errors"], list)
        assert result["validation"]["errors"]
        assert real_registry.get("dx_p3_bad_tool") is None


_LIVE_YAML_FOR_PATCH = (
    "name: dx_p4a_live\n"
    'version: "1.0.0"\n'
    "description: Live workflow used to exercise patch ops + version semantics.\n"
    "inputs:\n"
    "  - name: lookback\n"
    "    type: integer\n"
    "    default: 10\n"
    "steps:\n"
    "  - id: greet\n"
    "    tool: echo\n"
    "    mcp: system\n"
    "    params:\n"
    "      message: hi\n"
)


class TestWorkflowPatchSetOpAndVersionSemantics:
    """workflow_patch ``set`` op + previous_version + live-safety (S-292 P4a)."""

    @pytest.mark.asyncio
    async def test_set_op_on_input_default_promotes_with_previous_version(self, real_provider):
        raw_create = await real_provider.call(
            "workflow_create", {"yaml_content": _LIVE_YAML_FOR_PATCH}
        )
        assert _parse(raw_create)["status"] == "created"

        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_p4a_live",
                "version": "1.0.1",
                "operations": [{"op": "set", "path": "inputs.lookback.default", "value": 25}],
            },
        )
        result = _parse(raw)
        assert result["status"] == "patched"
        assert result["version"] == "1.0.1"
        assert result["previous_version"] == "1.0.0"
        assert result["patches_applied"] == 1
        assert result["validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_same_version_rejected_for_live_patch(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _LIVE_YAML_FOR_PATCH})

        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_p4a_live",
                    "version": "1.0.0",  # same as current
                    "operations": [{"op": "set", "path": "inputs.lookback.default", "value": 5}],
                },
            )
        # ``detail`` carries the human message; ``str()`` returns the
        # short ``message`` only, so look at the structured detail field.
        assert "must differ" in (exc_info.value.detail or "").lower()

    @pytest.mark.asyncio
    async def test_failed_live_patch_keeps_live_unchanged_creates_draft(
        self, real_registry, real_provider
    ):
        await real_provider.call("workflow_create", {"yaml_content": _LIVE_YAML_FOR_PATCH})

        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_p4a_live",
                "version": "1.0.1",
                # Set an unknown tool — validation will fail.
                "operations": [{"op": "set", "path": "steps.greet.tool", "value": "no_such_tool"}],
            },
        )
        result = _parse(raw)
        assert result["status"] == "draft"
        assert result["live_workflow_unchanged"] is True
        assert result["draft_id"]
        # The live registered workflow was never touched.
        live = real_registry.get("dx_p4a_live")
        assert live is not None
        assert live.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_set_op_on_step_param_succeeds(self, real_provider):
        await real_provider.call("workflow_create", {"yaml_content": _LIVE_YAML_FOR_PATCH})

        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_p4a_live",
                "version": "1.0.1",
                "operations": [
                    {
                        "op": "set",
                        "path": "steps.greet.params.message",
                        "value": "hello world",
                    }
                ],
            },
        )
        result = _parse(raw)
        assert result["status"] == "patched"
        assert result["previous_version"] == "1.0.0"


_TWO_STEP_YAML = (
    "name: dx_p4b_two\n"
    'version: "1.0.0"\n'
    "description: Two-step workflow used for add_step/remove_step coverage.\n"
    "steps:\n"
    "  - id: fetch\n"
    "    tool: echo\n"
    "    mcp: system\n"
    "    params:\n"
    "      message: fetch\n"
    "  - id: greet\n"
    "    tool: echo\n"
    "    mcp: system\n"
    "    params:\n"
    "      message: hi\n"
)


class TestWorkflowPatchAddRemoveStep:
    """workflow_patch ``add_step``/``remove_step`` ops (S-292 P4b)."""

    @pytest.mark.asyncio
    async def test_add_step_after_existing_step_inserts_at_position(self, real_provider):
        await real_provider.call("workflow_create", {"yaml_content": _TWO_STEP_YAML})
        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_p4b_two",
                "version": "1.0.1",
                "operations": [
                    {
                        "op": "add_step",
                        "after": "fetch",
                        "step": {
                            "id": "between",
                            "tool": "echo",
                            "mcp": "system",
                            "params": {"message": "between"},
                        },
                    }
                ],
            },
        )
        result = _parse(raw)
        assert result["status"] == "patched"
        # The patch tool description in tool_preview should reveal three
        # steps now — but at minimum the response should validate cleanly.
        assert result["validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_add_step_missing_id_rejected(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _TWO_STEP_YAML})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_p4b_two",
                    "version": "1.0.1",
                    "operations": [
                        {
                            "op": "add_step",
                            "after": "fetch",
                            "step": {"tool": "echo", "mcp": "system"},
                        }
                    ],
                },
            )
        assert "id" in (exc_info.value.detail or "").lower()

    @pytest.mark.asyncio
    async def test_remove_step_with_no_dependents_succeeds(self, real_provider):
        await real_provider.call("workflow_create", {"yaml_content": _TWO_STEP_YAML})
        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_p4b_two",
                "version": "1.0.1",
                "operations": [{"op": "remove_step", "step_id": "greet"}],
            },
        )
        result = _parse(raw)
        assert result["status"] == "patched"
        assert result["previous_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_max_patches_per_call_enforced(self, real_provider):
        from ploston_core.errors import AELError

        # Hard-cap the registry config to a small number; the fixture
        # provides a MagicMock so we can stomp this directly.
        real_provider._registry._config.max_patches_per_call = 3
        await real_provider.call("workflow_create", {"yaml_content": _TWO_STEP_YAML})
        ops = [
            {
                "op": "set",
                "path": "steps.greet.params.message",
                "value": f"v{i}",
            }
            for i in range(5)
        ]
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_p4b_two",
                    "version": "1.0.1",
                    "operations": ops,
                },
            )
        detail = (exc_info.value.detail or "").lower()
        assert "too many" in detail
        assert "max 3" in detail

    @pytest.mark.asyncio
    async def test_remove_step_with_dependents_rejected(self, real_provider):
        from ploston_core.errors import AELError

        yaml_with_deps = (
            "name: dx_p4b_deps\n"
            'version: "1.0.0"\n'
            "description: Two-step workflow where greet depends on fetch.\n"
            "steps:\n"
            "  - id: fetch\n"
            "    tool: echo\n"
            "    mcp: system\n"
            "    params:\n"
            "      message: fetch\n"
            "  - id: greet\n"
            "    tool: echo\n"
            "    mcp: system\n"
            "    depends_on: [fetch]\n"
            "    params:\n"
            "      message: hi\n"
        )
        await real_provider.call("workflow_create", {"yaml_content": yaml_with_deps})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_p4b_deps",
                    "version": "1.0.1",
                    "operations": [{"op": "remove_step", "step_id": "fetch"}],
                },
            )
        detail = (exc_info.value.detail or "").lower()
        assert "depends_on" in detail
        assert "greet" in detail


_PATCH_REPLACE_YAML = (
    "name: dx_replace_target\n"
    'version: "1.0.0"\n'
    "description: Code-only workflow used for replace-op error coverage.\n"
    "steps:\n"
    "  - id: compute\n"
    "    code: |\n"
    "      x = 1 + 1\n"
    "      y = x * 2\n"
    "      result = {'x': x, 'y': y}\n"
)


class TestWorkflowPatchOldNotFound:
    """``workflow_patch`` ``replace`` op surfaces structured context when
    ``old`` doesn't match the canonical step code.

    Exact-match path stays a normal success; missed-match raises
    ``INPUT_INVALID`` whose ``data`` payload echoes the canonical
    ``step_code`` and the agent's ``attempted_old``, plus an optional
    ``closest_match`` hint with ``differences`` classified as
    ``whitespace_only`` or ``content``.
    """

    @pytest.mark.asyncio
    async def test_exact_match_still_succeeds(self, real_provider):
        # Regression: enriching the failure path must not regress the
        # happy path.
        await real_provider.call("workflow_create", {"yaml_content": _PATCH_REPLACE_YAML})
        raw = await real_provider.call(
            "workflow_patch",
            {
                "name": "dx_replace_target",
                "version": "1.0.1",
                "operations": [
                    {
                        "op": "replace",
                        "step_id": "compute",
                        "old": "y = x * 2",
                        "new": "y = x * 3",
                    }
                ],
            },
        )
        assert _parse(raw)["status"] == "patched"

    @pytest.mark.asyncio
    async def test_old_not_found_returns_step_code_and_attempted_old(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _PATCH_REPLACE_YAML})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_replace_target",
                    "version": "1.0.1",
                    "operations": [
                        {
                            "op": "replace",
                            "step_id": "compute",
                            "old": "totally absent line",
                            "new": "irrelevant",
                        }
                    ],
                },
            )
        data = exc_info.value.data or {}
        assert data.get("step_id") == "compute"
        assert "x = 1 + 1" in (data.get("step_code") or "")
        assert data.get("attempted_old") == "totally absent line"

    @pytest.mark.asyncio
    async def test_whitespace_drift_classified_as_whitespace_only(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _PATCH_REPLACE_YAML})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_replace_target",
                    "version": "1.0.1",
                    "operations": [
                        {
                            "op": "replace",
                            "step_id": "compute",
                            # Same tokens as ``y = x * 2`` but with two
                            # leading spaces — exact substring won't match
                            # because the canonical code has no leading
                            # whitespace inside the YAML block scalar body.
                            "old": "  y = x * 2",
                            "new": "y = x * 3",
                        }
                    ],
                },
            )
        closest = (exc_info.value.data or {}).get("closest_match")
        assert closest is not None, "expected a closest_match hint"
        assert closest["differences"] == "whitespace_only"
        assert closest["match_ratio"] >= 0.6
        assert closest["line_range"] == [2, 2]

    @pytest.mark.asyncio
    async def test_content_drift_classified_as_content(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _PATCH_REPLACE_YAML})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_replace_target",
                    "version": "1.0.1",
                    "operations": [
                        {
                            "op": "replace",
                            "step_id": "compute",
                            # Token-level differences (``z`` vs ``y``,
                            # ``+`` vs ``*``) — not whitespace-only.
                            "old": "z = x + 2",
                            "new": "y = x * 3",
                        }
                    ],
                },
            )
        closest = (exc_info.value.data or {}).get("closest_match")
        assert closest is not None
        assert closest["differences"] == "content"

    @pytest.mark.asyncio
    async def test_unrelated_old_omits_closest_match(self, real_provider):
        from ploston_core.errors import AELError

        await real_provider.call("workflow_create", {"yaml_content": _PATCH_REPLACE_YAML})
        with pytest.raises(AELError) as exc_info:
            await real_provider.call(
                "workflow_patch",
                {
                    "name": "dx_replace_target",
                    "version": "1.0.1",
                    "operations": [
                        {
                            "op": "replace",
                            "step_id": "compute",
                            # Nothing in the code body resembles this.
                            "old": "QQQQ unrelated payload zzzz",
                            "new": "irrelevant",
                        }
                    ],
                },
            )
        data = exc_info.value.data or {}
        assert "step_code" in data
        assert "closest_match" not in data


# ── S-291 (P3): per-error-type roundtrip coverage ─────────────────
# Spec §"Unit roundtrip per error type (12 tests)" — induce each
# catalog error, take the response's ``suggested_fix`` (or assert
# ``requires_agent_decision``), apply via ``workflow_patch`` on the
# returned ``draft_id``, and verify the error is resolved.


@pytest.fixture
def strict_tool_registry():
    """Tool registry whose ``list_tools(server_name=…)`` filters by mcp.

    Required so the validator can produce ``unknown_mcp`` errors —
    the simpler ``real_tool_registry`` returns the same tool list for
    any server name and so masks server-not-found cases.
    """
    from ploston_core.registry.types import ToolDefinition
    from ploston_core.types import ToolSource, ToolStatus

    echo = ToolDefinition(
        name="echo",
        description="Echo a message back.",
        source=ToolSource.SYSTEM,
        server_name="system",
        input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        status=ToolStatus.AVAILABLE,
    )
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()
    tr.get.return_value = None

    def _list(server_name: str | None = None):
        if server_name in (None, "system"):
            return [echo]
        return []

    tr.list_tools.side_effect = _list
    return tr


@pytest.fixture
def strict_registry(tmp_path, strict_tool_registry):
    from ploston_core.workflow.registry import WorkflowRegistry

    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    config.draft_ttl_seconds = 1800
    return WorkflowRegistry(strict_tool_registry, config)


@pytest.fixture
def strict_provider(strict_registry, strict_tool_registry):
    return WorkflowToolsProvider(
        workflow_registry=strict_registry,
        tool_registry=strict_tool_registry,
    )


def _find_error(errors: list[dict], **predicates) -> dict | None:
    """Return the first error whose fields match all ``predicates``."""
    for e in errors:
        if all(e.get(k) == v for k, v in predicates.items()):
            return e
    return None


async def _create_and_get_errors(provider, yaml: str) -> tuple[str | None, list[dict]]:
    raw = await provider.call("workflow_create", {"yaml_content": yaml})
    body = _parse(raw)
    return body.get("draft_id"), body.get("validation", {}).get("errors", [])


async def _apply_fix(provider, draft_id: str, fix: dict) -> dict:
    raw = await provider.call("workflow_patch", {"draft_id": draft_id, "operations": [fix]})
    return _parse(raw)


class TestWorkflowAuthoringRoundtrips:
    """12 per-error-type roundtrip tests (spec §S-291 P3)."""

    @pytest.mark.asyncio
    async def test_roundtrip_unknown_tool(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_unk_tool\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    tool: ehco\n    mcp: system\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.tool")
        assert err and err["suggested_fix"]["value"] == "echo"

        result = await _apply_fix(strict_provider, draft_id, err["suggested_fix"])
        assert result["status"] == "patched"
        assert strict_registry.get("rt_unk_tool") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_unknown_mcp(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_unk_mcp\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    tool: echo\n    mcp: systom\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.mcp")
        assert err and err["suggested_fix"]["value"] == "system"

        result = await _apply_fix(strict_provider, draft_id, err["suggested_fix"])
        assert result["status"] == "patched"
        assert strict_registry.get("rt_unk_mcp") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_return_in_code(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_return\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    code: |\n      x = 1\n      return {'x': x}\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.code")
        assert err and err["suggested_fix"]["op"] == "replace"
        assert "result =" in err["suggested_fix"]["new"]

        result = await _apply_fix(strict_provider, draft_id, err["suggested_fix"])
        assert result["status"] == "patched"
        assert strict_registry.get("rt_return") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_forbidden_import(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_forb_imp\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    code: |\n      import socket\n      result = 1\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.code")
        # ``forbidden_import`` is deterministic — the suggested_fix removes
        # the offending line.
        assert err and err["suggested_fix"]["op"] == "replace"
        assert err["suggested_fix"]["new"] == ""

        result = await _apply_fix(strict_provider, draft_id, err["suggested_fix"])
        assert result["status"] == "patched"
        assert strict_registry.get("rt_forb_imp") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_forbidden_builtin(self, strict_provider):
        yaml = (
            'name: rt_forb_bi\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    code: |\n      result = eval('1')\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.code")
        # ``forbidden_builtin`` has no deterministic fix — agent must
        # rewrite the expression. Verify the response signals that.
        assert err and err["suggested_fix"] is None
        assert err["requires_agent_decision"] is True

    @pytest.mark.asyncio
    async def test_roundtrip_missing_field(self, strict_provider):
        # Missing top-level ``name`` surfaces as a parse error — the
        # spec catalog's missing_required path. Agents must supply the
        # field; the response flags requires_agent_decision.
        yaml = (
            'version: "1.0.0"\ndescription: x\nsteps:\n  - id: a\n    tool: echo\n    mcp: system\n'
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="yaml")
        assert err and err["requires_agent_decision"] is True

    @pytest.mark.asyncio
    async def test_roundtrip_invalid_type(self, strict_provider):
        # ``steps`` must be a list — string here exercises the parse
        # path with a different error class than missing_field.
        yaml = 'name: rt_bad_type\nversion: "1.0.0"\ndescription: x\nsteps: notalist\n'
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="yaml")
        assert err and err["requires_agent_decision"] is True

    @pytest.mark.asyncio
    async def test_roundtrip_template_unknown_step(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_tmpl_step\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n"
            "  - id: a\n    tool: echo\n    mcp: system\n    params:\n      m: hi\n"
            "  - id: b\n    tool: echo\n    mcp: system\n    depends_on: [a]\n"
            '    params:\n      m: "{{ steps.aa.output }}"\n'
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.b.params")
        assert err and "alternatives" in err and err["alternatives"] == ["a"]
        assert err["requires_agent_decision"] is True

        # The agent applies the resolution by editing the param value
        # directly — workflow_patch ``set`` of the param.
        fix = {
            "op": "set",
            "path": "steps.b.params.m",
            "value": "{{ steps.a.output }}",
        }
        result = await _apply_fix(strict_provider, draft_id, fix)
        assert result["status"] == "patched"
        assert strict_registry.get("rt_tmpl_step") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_template_unknown_input(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_tmpl_inp\nversion: "1.0.0"\ndescription: x\n'
            "inputs:\n  - name:\n      type: string\n      required: true\n"
            "steps:\n  - id: a\n    tool: echo\n    mcp: system\n    params:\n"
            '      m: "{{ inputs.namee }}"\n'
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.params")
        assert err and err["alternatives"] == ["name"]
        assert err["requires_agent_decision"] is True

        fix = {
            "op": "set",
            "path": "steps.a.params.m",
            "value": "{{ inputs.name }}",
        }
        result = await _apply_fix(strict_provider, draft_id, fix)
        assert result["status"] == "patched"
        assert strict_registry.get("rt_tmpl_inp") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_duplicate_step_id(self, strict_provider):
        yaml = (
            'name: rt_dup\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n"
            "  - id: a\n    tool: echo\n    mcp: system\n"
            "  - id: a\n    tool: echo\n    mcp: system\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps")
        assert err and err["requires_agent_decision"] is True
        assert err.get("current_value") == "a"

    @pytest.mark.asyncio
    async def test_roundtrip_bad_depends_on(self, strict_provider, strict_registry):
        yaml = (
            'name: rt_bad_dep\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    tool: echo\n    mcp: system\n"
            "    depends_on: [missing]\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="steps.a.depends_on")
        assert err and err["suggested_fix"]["op"] == "set"
        assert err["suggested_fix"]["value"] == []

        result = await _apply_fix(strict_provider, draft_id, err["suggested_fix"])
        assert result["status"] == "patched"
        assert strict_registry.get("rt_bad_dep") is not None

    @pytest.mark.asyncio
    async def test_roundtrip_reserved_input_name(self, strict_provider):
        yaml = (
            'name: rt_resv\nversion: "1.0.0"\ndescription: x\n'
            "inputs:\n  - context:\n      type: string\n"
            "steps:\n  - id: a\n    tool: echo\n    mcp: system\n"
        )
        draft_id, errors = await _create_and_get_errors(strict_provider, yaml)
        assert draft_id
        err = _find_error(errors, path="inputs.context")
        # ``reserved_input_name`` collisions need the agent to choose a
        # new name — no deterministic fix.
        assert err and err["requires_agent_decision"] is True
        assert err["suggested_fix"] is None


class _FakeInstrument:
    """Records ``add``/``record`` calls for assertion in tests."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.calls: list[tuple[float, dict]] = []

    def add(self, amount: float, attributes: dict | None = None) -> None:
        self.calls.append((amount, dict(attributes or {})))

    def record(self, amount: float, attributes: dict | None = None) -> None:
        self.calls.append((amount, dict(attributes or {})))


class _FakeMeter:
    """Minimal meter that returns ``_FakeInstrument`` for both kinds."""

    def __init__(self) -> None:
        self.instruments: dict[str, _FakeInstrument] = {}

    def create_counter(self, *, name: str, **_: object) -> _FakeInstrument:
        inst = _FakeInstrument(name, "counter")
        self.instruments[name] = inst
        return inst

    def create_histogram(self, *, name: str, **_: object) -> _FakeInstrument:
        inst = _FakeInstrument(name, "histogram")
        self.instruments[name] = inst
        return inst


class TestAuthoringMetrics:
    """M-081 Measurement Plan: six OTel meters on WorkflowToolsProvider."""

    @pytest.fixture
    def metered_provider(self, strict_provider):
        meter = _FakeMeter()
        strict_provider.set_meter(meter)
        return strict_provider, meter

    def test_six_instruments_registered(self, metered_provider):
        _, meter = metered_provider
        assert "ploston_workflow_schema_response_bytes" in meter.instruments
        assert "ploston_workflow_create_roundtrips_total" in meter.instruments
        assert "ploston_workflow_patch_calls_total" in meter.instruments
        assert "ploston_draft_created_total" in meter.instruments
        assert "ploston_draft_promoted_total" in meter.instruments
        assert "ploston_suggested_fix_accepted_total" in meter.instruments
        assert "ploston_suggested_fix_rejected_total" in meter.instruments

    @pytest.mark.asyncio
    async def test_schema_handler_records_response_bytes(self, metered_provider):
        provider, meter = metered_provider
        # Tier 1 (no section)
        await provider._handle_schema({})
        bytes_inst = meter.instruments["ploston_workflow_schema_response_bytes"]
        assert len(bytes_inst.calls) == 1
        size, attrs = bytes_inst.calls[0]
        assert size > 0
        assert attrs == {"section": "tier1"}

        # Specific section
        section = next(iter(AVAILABLE_SECTIONS))
        await provider._handle_schema({"section": section})
        assert len(bytes_inst.calls) == 2
        _, attrs2 = bytes_inst.calls[1]
        assert attrs2 == {"section": section}

    @pytest.mark.asyncio
    async def test_create_records_roundtrip_and_draft(self, metered_provider):
        provider, meter = metered_provider
        # Invalid YAML produces draft + create roundtrip.
        bad_yaml = 'name: bad\nversion: "1.0.0"\nsteps: notalist\n'
        await provider._handle_create({"yaml_content": bad_yaml})
        rt = meter.instruments["ploston_workflow_create_roundtrips_total"]
        drafts = meter.instruments["ploston_draft_created_total"]
        assert len(rt.calls) == 1
        assert rt.calls[0][1] == {"status": "draft"}
        assert len(drafts.calls) == 1

    @pytest.mark.asyncio
    async def test_patch_records_promotion(self, metered_provider):
        provider, meter = metered_provider
        # Create an invalid YAML, then patch it to promote.
        bad_yaml = (
            'name: rt_metrics\nversion: "1.0.0"\ndescription: x\n'
            "steps:\n  - id: a\n    tool: nope\n    mcp: system\n"
        )
        create_resp = await provider._handle_create({"yaml_content": bad_yaml})
        draft_id = create_resp["draft_id"]
        # Patch with a valid tool name to promote.
        await provider._handle_patch(
            {
                "draft_id": draft_id,
                "operations": [{"op": "set", "path": "steps.a.tool", "value": "echo"}],
            }
        )
        patch_calls = meter.instruments["ploston_workflow_patch_calls_total"]
        promoted = meter.instruments["ploston_draft_promoted_total"]
        assert len(patch_calls.calls) == 1
        assert patch_calls.calls[0][1] == {"target": "draft", "status": "patched"}
        assert len(promoted.calls) == 1

    def test_no_meter_is_noop(self, strict_provider):
        # No set_meter call → recording is silent; no exception.
        am = strict_provider._authoring_metrics
        am.record_schema_response_bytes(100)
        am.record_workflow_create(status="created")
        am.record_workflow_patch(target="live", status="patched")
        am.record_draft_created()
        am.record_draft_promoted()
        am.record_suggested_fix(accepted=True, kind="x")
        am.record_suggested_fix(accepted=False, kind="x")
        assert am.is_enabled is False
