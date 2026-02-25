"""Tool access filtering for Pro Auth Foundation.

Implements PRO_AUTH_FOUNDATION_SPEC tool access control:
- Filter tools/list by principal's tool_access
- Validate tool access before execution
- Intersection with bridge --filter-servers

Server-level granularity for Pro — per-tool granularity is Enterprise.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import PrincipalContext, ToolAccess, ToolAccessMode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def filter_tools_by_principal(
    tools: list[Any],  # list[ToolInfo]
    principal_context: PrincipalContext | None,
    bridge_filter_servers: list[str] | None = None,
) -> list[Any]:
    """Filter tools based on principal's tool_access.

    Args:
        tools: List of ToolInfo objects
        principal_context: Request's principal context (None for OSS mode)
        bridge_filter_servers: Optional bridge --filter-servers list

    Returns:
        Filtered list of tools the principal can access
    """
    if not principal_context:
        # OSS mode: no filtering
        return tools

    tool_access = principal_context.principal.tool_access

    # If both principal and bridge have filters, use intersection
    effective_access = _compute_effective_access(tool_access, bridge_filter_servers)

    # Filter tools by server name
    filtered = []
    for tool in tools:
        server_name = getattr(tool, "server_name", None)
        if server_name is None:
            # System/native tools without server - allow if mode is ALL
            if effective_access.mode == ToolAccessMode.ALL:
                filtered.append(tool)
            continue

        if effective_access.can_access_server(server_name):
            filtered.append(tool)

    return filtered


def _compute_effective_access(
    principal_access: ToolAccess,
    bridge_filter_servers: list[str] | None,
) -> ToolAccess:
    """Compute effective tool access from principal + bridge filters.

    Effective tool access = intersection(principal.tool_access, bridge.filter_servers)

    Args:
        principal_access: Principal's tool_access config
        bridge_filter_servers: Bridge --filter-servers list (if any)

    Returns:
        Effective ToolAccess for filtering
    """
    if not bridge_filter_servers:
        return principal_access

    # Bridge filter is always an allowlist
    bridge_access = ToolAccess(
        mode=ToolAccessMode.ALLOWLIST,
        servers=bridge_filter_servers,
    )

    # Compute intersection
    if principal_access.mode == ToolAccessMode.ALL:
        # Principal allows all, use bridge filter
        return bridge_access

    if principal_access.mode == ToolAccessMode.ALLOWLIST:
        # Both are allowlists, intersection
        intersection = [s for s in principal_access.servers if s in bridge_filter_servers]
        return ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=intersection)

    if principal_access.mode == ToolAccessMode.DENYLIST:
        # Principal denies some, bridge allows some
        # Result: bridge allowlist minus principal denylist
        allowed = [s for s in bridge_filter_servers if s not in principal_access.servers]
        return ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=allowed)

    return principal_access


def can_access_tool(
    tool_server: str | None,
    principal_context: PrincipalContext | None,
    bridge_filter_servers: list[str] | None = None,
) -> bool:
    """Check if principal can access a specific tool.

    Args:
        tool_server: Server name the tool belongs to (None for system tools)
        principal_context: Request's principal context
        bridge_filter_servers: Optional bridge --filter-servers list

    Returns:
        True if access is allowed
    """
    if not principal_context:
        # OSS mode: allow all
        return True

    if tool_server is None:
        # System/native tools - allow if mode is ALL
        return principal_context.principal.tool_access.mode == ToolAccessMode.ALL

    effective_access = _compute_effective_access(
        principal_context.principal.tool_access,
        bridge_filter_servers,
    )

    return effective_access.can_access_server(tool_server)
