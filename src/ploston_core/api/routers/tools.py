"""Tool router."""

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
                        # Tool info can be a string or a dict with name/description
                        if isinstance(tool_info, str):
                            tool_name = tool_info
                            tool_desc = f"Tool from runner '{runner.name}'"
                        else:
                            tool_name = tool_info.get("name", str(tool_info))
                            tool_desc = tool_info.get(
                                "description", f"Tool from runner '{runner.name}'"
                            )

                        # Apply search filter to runner tools too
                        if search:
                            search_lower = search.lower()
                            if (
                                search_lower not in tool_name.lower()
                                and search_lower not in tool_desc.lower()
                            ):
                                continue

                        # Runner tools from connected runners are always available
                        summaries.append(
                            ToolSummary(
                                name=tool_name,
                                source=ToolSource.RUNNER,
                                server=runner.name,
                                description=tool_desc,
                                category=None,
                                status=ToolStatus.AVAILABLE,
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
    """Get tool schema."""
    registry = request.app.state.tool_registry
    tool = registry.get(tool_name)

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    return ToolDetail(
        name=tool.name,
        source=_convert_source(tool.source),
        server=tool.server_name,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
    )


@tool_router.post("/{tool_name}/call", response_model=ToolCallResponse)
async def call_tool(
    request: Request,
    tool_name: str = Path(...),
    call_request: ToolCallRequest = Body(...),
) -> ToolCallResponse:
    """Call a tool directly (for testing/debugging)."""
    invoker = request.app.state.tool_invoker

    try:
        result = await invoker.invoke(tool_name, call_request.params)

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
        raise HTTPException(status_code=e.http_status, detail=e.to_dict())
