"""Tool router."""

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Path, Request

from ploston_core.api.models import (
    RefreshServerResult,
    ToolCallRequest,
    ToolCallResponse,
    ToolDetail,
    ToolListResponse,
    ToolRefreshResponse,
    ToolSource,
    ToolStatus,
    ToolSummary,
)
from ploston_core.errors import AELError
from ploston_core.telemetry.store.types import (
    ErrorRecord as TelemetryErrorRecord,
)
from ploston_core.telemetry.store.types import (
    ExecutionStatus as TelemetryExecutionStatus,
)
from ploston_core.telemetry.store.types import ExecutionType, ToolCallSource
from ploston_core.telemetry.store.wrappers import (
    record_tool_call,
    synthetic_direct_step,
)
from ploston_core.types import ToolSource as InternalToolSource
from ploston_core.types import ToolStatus as InternalToolStatus

tool_router = APIRouter(prefix="/tools", tags=["Tools"])


def _convert_source(source: InternalToolSource) -> ToolSource:
    """Convert internal ToolSource to API ToolSource."""
    mapping = {
        InternalToolSource.MCP: ToolSource.MCP,
        InternalToolSource.SYSTEM: ToolSource.SYSTEM,
        InternalToolSource.NATIVE: ToolSource.NATIVE,
    }
    return mapping.get(source, ToolSource.MCP)


def _convert_status(status: InternalToolStatus) -> ToolStatus:
    """Convert internal ToolStatus to API ToolStatus."""
    if status == InternalToolStatus.AVAILABLE:
        return ToolStatus.AVAILABLE
    return ToolStatus.UNAVAILABLE


@tool_router.get("", response_model=ToolListResponse)
async def list_tools(
    request: Request,
    source: ToolSource | None = None,
    server: str | None = None,
    search: str | None = None,
    include_runner: bool = True,
) -> ToolListResponse:
    """List available tools.

    Args:
        source: Filter by tool source (mcp, system, native, runner)
        server: Filter by server name
        search: Search in tool name/description
        include_runner: Include tools from connected runners (default: True)
    """
    registry = request.app.state.tool_registry
    tools = registry.list_tools()

    # Filter by source (for non-runner tools)
    if source and source != ToolSource.RUNNER:
        internal_source = InternalToolSource(source.value)
        tools = [t for t in tools if t.source == internal_source]

    # Filter by server
    if server:
        tools = [t for t in tools if t.server_name == server]

    # Filter by search
    if search:
        search_lower = search.lower()
        tools = [
            t
            for t in tools
            if search_lower in t.name.lower()
            or (t.description and search_lower in t.description.lower())
        ]

    summaries = [
        ToolSummary(
            name=t.name,
            source=_convert_source(t.source),
            server=t.server_name,
            description=t.description,
            tags=sorted(t.tags),
            status=_convert_status(t.status),
            input_schema=t.input_schema or {},
            output_schema=t.output_schema,
            has_learned_output_schema=(
                t.output_schema is None
                and registry.get_suggested_schema(t.name, min_confidence=0.0) is not None
            ),
        )
        for t in tools
    ]

    # Add runner tools if requested and not filtering by non-runner source
    if include_runner and (source is None or source == ToolSource.RUNNER):
        runner_registry = getattr(request.app.state, "runner_registry", None)
        if runner_registry:
            for runner in runner_registry.list():
                # Only include tools from connected runners
                if runner.status.value == "connected" and runner.available_tools:
                    for tool_info in runner.available_tools:
                        # Tool info can be a string or a dict with
                        # name/description/inputSchema/outputSchema. Runners
                        # mirror the upstream MCP shape, so the JSON Schema
                        # keys are camelCase (``inputSchema``).
                        tool_input_schema: dict[str, Any] = {}
                        tool_output_schema: dict[str, Any] | None = None
                        if isinstance(tool_info, str):
                            tool_name = tool_info
                            tool_desc = f"Tool from runner '{runner.name}'"
                        else:
                            tool_name = tool_info.get("name", str(tool_info))
                            tool_desc = tool_info.get(
                                "description", f"Tool from runner '{runner.name}'"
                            )
                            tool_input_schema = tool_info.get("inputSchema") or {}
                            tool_output_schema = tool_info.get("outputSchema")

                        # Apply search filter to runner tools too
                        if search:
                            search_lower = search.lower()
                            if (
                                search_lower not in tool_name.lower()
                                and search_lower not in tool_desc.lower()
                            ):
                                continue

                        # Runner tools from connected runners are always available.
                        # F-088: surface learned schemas for runner-hosted tools
                        # too. The schema store keys observations by the bare
                        # ``(<mcp>, <tool>)`` pair derived from the canonical
                        # name; ``get_suggested_schema`` decodes that on our
                        # behalf when ``server_name`` is omitted.
                        runner_has_learned = (
                            tool_output_schema is None
                            and registry.get_suggested_schema(tool_name, min_confidence=0.0)
                            is not None
                        )
                        summaries.append(
                            ToolSummary(
                                name=tool_name,
                                source=ToolSource.RUNNER,
                                server=runner.name,
                                description=tool_desc,
                                status=ToolStatus.AVAILABLE,
                                input_schema=tool_input_schema,
                                output_schema=tool_output_schema,
                                has_learned_output_schema=runner_has_learned,
                            )
                        )

    return ToolListResponse(tools=summaries, total=len(summaries))


@tool_router.post("/refresh", response_model=ToolRefreshResponse)
async def refresh_tools(
    request: Request,
    server: str | None = None,
) -> ToolRefreshResponse:
    """Refresh tool schemas from all sources, or a single server when ``server`` is set."""
    registry = request.app.state.tool_registry

    try:
        if server is not None:
            result = await registry.refresh_server(server)
        else:
            result = await registry.refresh()

        servers: dict[str, RefreshServerResult] = {}
        for server_name, error in result.errors.items():
            servers[server_name] = RefreshServerResult(status="error", error=error)

        # Mark successful servers
        scoped_tools = (
            [t for t in registry.list_tools() if t.server_name == server]
            if server is not None
            else registry.list_tools()
        )
        for tool in scoped_tools:
            if tool.server_name and tool.server_name not in servers:
                if tool.server_name not in servers:
                    servers[tool.server_name] = RefreshServerResult(status="ok", tools=0)
                servers[tool.server_name].tools = (servers[tool.server_name].tools or 0) + 1

        return ToolRefreshResponse(refreshed=result.total_tools, servers=servers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tool_router.get("/{tool_name}", response_model=ToolDetail)
async def get_tool(
    request: Request,
    tool_name: str = Path(...),
) -> ToolDetail:
    """Get tool schema.

    Resolves CP-tracked tools first; falls back to the runner registry so
    runner-hosted tools (which never enter ``ToolRegistry``) can still
    surface their input/output schemas and any learned schema F-088 has
    accumulated.
    """
    registry = request.app.state.tool_registry
    tool = registry.get(tool_name)

    if tool is not None:
        return ToolDetail(
            name=tool.name,
            source=_convert_source(tool.source),
            server=tool.server_name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            suggested_output_schema=registry.get_suggested_schema(tool.name, min_confidence=0.0),
        )

    runner_match = _find_runner_tool(request, tool_name)
    if runner_match is not None:
        runner_name, info = runner_match
        if isinstance(info, str):
            description = f"Tool from runner '{runner_name}'"
            input_schema: dict[str, Any] = {}
            output_schema: dict[str, Any] | None = None
        else:
            description = info.get("description") or f"Tool from runner '{runner_name}'"
            input_schema = info.get("inputSchema") or {}
            output_schema = info.get("outputSchema")
        return ToolDetail(
            name=tool_name,
            source=ToolSource.RUNNER,
            server=runner_name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            suggested_output_schema=registry.get_suggested_schema(tool_name, min_confidence=0.0),
        )

    raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")


def _find_runner_tool(request: Request, tool_name: str) -> tuple[str, str | dict[str, Any]] | None:
    """Locate ``tool_name`` on any connected runner; return ``(runner_name, info)``.

    Mirrors the matching logic in ``list_tools`` so detail/list stay
    consistent. Returns ``None`` when no connected runner advertises the
    tool.
    """
    runner_registry = getattr(request.app.state, "runner_registry", None)
    if runner_registry is None:
        return None
    for runner in runner_registry.list():
        if runner.status.value != "connected" or not runner.available_tools:
            continue
        for entry in runner.available_tools:
            entry_name = entry if isinstance(entry, str) else entry.get("name")
            if entry_name == tool_name:
                return runner.name, entry
    return None


@tool_router.post("/{tool_name}/call", response_model=ToolCallResponse)
async def call_tool(
    request: Request,
    tool_name: str = Path(...),
    call_request: ToolCallRequest = Body(...),
) -> ToolCallResponse:
    """Call a tool directly (for testing/debugging)."""
    invoker = request.app.state.tool_invoker
    collector = getattr(request.app.state, "telemetry_collector", None)

    # S-304/M-082: REST clients aren't MCP clients; honour explicit headers if
    # set (proxies, smoke tests) but otherwise leave session_id NULL.
    _rest_bridge_id = request.headers.get("X-Bridge-ID")
    _rest_session_id = request.headers.get("X-MCP-Session-ID")

    # S-304 / G6 — record DIRECT execution + synthetic step + tool_call row
    execution_id: str | None = None
    if collector is not None:
        try:
            execution_id = await collector.start_execution(
                execution_type=ExecutionType.DIRECT,
                tool_name=tool_name,
                source="rest",
                session_id=_rest_session_id,
                bridge_session_id=_rest_bridge_id,
            )
        except Exception:
            pass  # Telemetry never blocks the user's call

    try:
        async with synthetic_direct_step(
            collector,
            execution_id=execution_id,
            tool_name=tool_name,
        ) as _step_id:
            async with record_tool_call(
                collector,
                execution_id=execution_id,
                step_id=_step_id,
                tool_name=tool_name,
                params=call_request.params,
                source=ToolCallSource.DIRECT,
                bridge_id=_rest_bridge_id,
                session_id=_rest_session_id,
            ) as _call_handle:
                result = await invoker.invoke(tool_name, call_request.params)
                if result.success:
                    _call_handle.set_result(result.output)
                elif result.error is not None:
                    _call_handle.set_error(
                        TelemetryErrorRecord(
                            code=getattr(result.error, "code", "UNKNOWN"),
                            category="tool",
                            message=getattr(result.error, "message", str(result.error)),
                        )
                    )

        if collector is not None and execution_id is not None:
            try:
                if result.success:
                    await collector.end_execution(
                        execution_id=execution_id,
                        status=TelemetryExecutionStatus.COMPLETED,
                    )
                else:
                    await collector.end_execution(
                        execution_id=execution_id,
                        status=TelemetryExecutionStatus.FAILED,
                        error=TelemetryErrorRecord(
                            code=getattr(result.error, "code", "UNKNOWN")
                            if result.error
                            else "UNKNOWN",
                            category="tool",
                            message=getattr(result.error, "message", str(result.error))
                            if result.error
                            else "unknown",
                        ),
                    )
            except Exception:
                pass

        if not result.success:
            raise HTTPException(
                status_code=502,
                detail=result.error.to_dict() if result.error else "Tool call failed",
            )

        return ToolCallResponse(
            tool_name=tool_name,
            duration_ms=result.duration_ms,
            result=result.output,
        )
    except AELError as e:
        if collector is not None and execution_id is not None:
            try:
                await collector.end_execution(
                    execution_id=execution_id,
                    status=TelemetryExecutionStatus.FAILED,
                    error=TelemetryErrorRecord(
                        code=e.code,
                        category="tool",
                        message=str(e),
                    ),
                )
            except Exception:
                pass
        raise HTTPException(status_code=e.http_status, detail=e.to_dict())
