"""Health and info router."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ploston_core.api.models import HealthCheck, HealthStatus, ServerInfo

health_router = APIRouter(tags=["Health"])


@health_router.get("/health", response_model=HealthCheck)
async def health_check(request: Request) -> HealthCheck:
    """Check AEL health status."""
    checks: dict[str, str] = {}

    # Check workflow registry
    workflow_registry = request.app.state.workflow_registry
    if workflow_registry:
        try:
            workflow_registry.list_workflows()
            checks["workflow_registry"] = "ok"
        except Exception as e:
            checks["workflow_registry"] = f"error: {e}"
    else:
        checks["workflow_registry"] = "not_configured"

    # Check tool registry
    tool_registry = request.app.state.tool_registry
    if tool_registry:
        try:
            tool_registry.list_tools()
            checks["tool_registry"] = "ok"
        except Exception as e:
            checks["tool_registry"] = f"error: {e}"
    else:
        checks["tool_registry"] = "not_configured"

    # Determine overall status
    all_ok = all(v == "ok" or v == "not_configured" for v in checks.values())
    any_error = any("error" in v for v in checks.values())

    if any_error:
        status = HealthStatus.UNHEALTHY
    elif all_ok:
        status = HealthStatus.HEALTHY
    else:
        status = HealthStatus.DEGRADED

    return HealthCheck(
        status=status,
        checks=checks,
        timestamp=datetime.now(timezone.utc),
    )


@health_router.get("/info", response_model=ServerInfo)
async def server_info(request: Request) -> ServerInfo:
    """Get server information."""
    config = request.app.state.config

    # Determine features
    features = {
        "workflows": True,
        "tools": True,
        "python_exec": True,
        "rate_limiting": config.rate_limiting_enabled,
        "authentication": config.require_auth,
    }

    # MCP info
    mcp_info = {
        "protocol_version": "2024-11-05",
        "transport": "http",
    }

    return ServerInfo(
        name="AEL",
        version=config.version,
        edition="oss",
        features=features,
        mcp=mcp_info,
    )

