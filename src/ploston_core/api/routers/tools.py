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
    ToolSummary,
)
from ploston_core.errors import AELError
from ploston_core.types import ToolSource as InternalToolSource

tool_router = APIRouter(prefix="/tools", tags=["Tools"])


def _convert_source(source: InternalToolSource) -> ToolSource:
    """Convert internal ToolSource to API ToolSource."""
    mapping = {
        InternalToolSource.MCP: ToolSource.MCP,
        InternalToolSource.SYSTEM: ToolSource.SYSTEM,
    }
    return mapping.get(source, ToolSource.MCP)


@tool_router.get("", response_model=ToolListResponse)
async def list_tools(
    request: Request,
    source: ToolSource | None = None,
    server: str | None = None,
    search: str | None = None,
) -> ToolListResponse:
    """List available tools."""
    registry = request.app.state.tool_registry
    tools = registry.list_tools()

    # Filter by source
    if source:
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
            category=t.category,
        )
        for t in tools
    ]

    return ToolListResponse(tools=summaries, total=len(summaries))


@tool_router.post("/refresh", response_model=ToolRefreshResponse)
async def refresh_tools(request: Request) -> ToolRefreshResponse:
    """Refresh tool schemas from all sources."""
    registry = request.app.state.tool_registry

    try:
        result = await registry.refresh()

        servers: dict[str, RefreshServerResult] = {}
        for server_name, error in result.errors.items():
            servers[server_name] = RefreshServerResult(status="error", error=error)

        # Mark successful servers
        for tool in registry.list_tools():
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
    tool = registry.get_tool(tool_name)

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
