"""MCP servers router.

Per-server runtime status for CP-hosted MCP servers.
"""

from fastapi import APIRouter, HTTPException, Path, Request

from ploston_core.api.models import MCPServerStatusResponse

mcp_servers_router = APIRouter(prefix="/mcp-servers", tags=["MCP Servers"])


@mcp_servers_router.get("/{name}/status", response_model=MCPServerStatusResponse)
async def get_mcp_server_status(
    request: Request,
    name: str = Path(..., description="CP-hosted MCP server name"),
) -> MCPServerStatusResponse:
    """Return runtime status for a single CP-hosted MCP server."""
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is None:
        raise HTTPException(status_code=503, detail="MCP manager not available")

    statuses = mcp_manager.get_status()
    server_status = statuses.get(name)
    if server_status is None:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{name}' not found",
        )

    status_value = getattr(server_status.status, "value", server_status.status)

    return MCPServerStatusResponse(
        name=server_status.name,
        status=str(status_value),
        tool_count=len(server_status.tools),
        last_connected_at=server_status.last_connected,
        error=server_status.last_error or server_status.error,
    )
