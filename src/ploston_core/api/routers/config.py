"""Config router for Ploston API.

Implements:
- GET /config - Get current configuration
- GET /config/diff - Get diff between current and staged config
- POST /config/set - Stage a configuration change (T-633)
- POST /config/done - Apply staged configuration (T-634)
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

config_router = APIRouter(prefix="/config", tags=["config"])


class ConfigDiffResponse(BaseModel):
    """Response for config diff endpoint."""

    diff: str
    has_changes: bool
    in_config_mode: bool


class ConfigSetRequest(BaseModel):
    """Request body for config/set endpoint."""

    path: str = Field(..., description="Dot-separated config path (e.g., 'runners.local')")
    value: Any = Field(..., description="Value to set at the path")


class ConfigSetResponse(BaseModel):
    """Response for config/set endpoint."""

    staged: bool
    path: str | None = None
    error: str | None = None
    validation: dict[str, Any] | None = None


class ConfigDoneResponse(BaseModel):
    """Response for config/done endpoint."""

    success: bool
    mode: str
    config_written_to: str | None = None
    errors: list[dict[str, Any]] | None = None
    capabilities: dict[str, Any] | None = None
    message: str | None = None


@config_router.get("")
async def get_config(
    request: Request,
    section: str | None = Query(None, description="Specific section to retrieve"),
) -> dict[str, Any]:
    """Get server configuration.

    Returns the current server configuration. If a section is specified,
    only that section is returned.
    """
    # Get config from app state
    config = getattr(request.app.state, "config", {})

    # Convert config object to dict if needed
    if hasattr(config, "to_dict"):
        config_dict = config.to_dict()
    elif hasattr(config, "__dict__"):
        config_dict = {k: v for k, v in config.__dict__.items() if not k.startswith("_")}
    else:
        config_dict = dict(config) if config else {}

    # If section specified, return only that section
    if section:
        return {section: config_dict.get(section, {})}

    return config_dict


@config_router.get("/diff", response_model=ConfigDiffResponse)
async def get_config_diff(request: Request) -> ConfigDiffResponse:
    """Get diff between current config and staged changes.

    Only meaningful in configuration mode. Returns empty diff in running mode.
    """
    # Check mode
    mode_manager = getattr(request.app.state, "mode_manager", None)
    in_config_mode = mode_manager.is_configuration_mode() if mode_manager else False

    if not in_config_mode:
        return ConfigDiffResponse(
            diff="",
            has_changes=False,
            in_config_mode=False,
        )

    # Get staged config
    staged_config = getattr(request.app.state, "staged_config", None)
    if not staged_config:
        raise HTTPException(status_code=503, detail="Staged config not available")

    diff = staged_config.get_diff()
    has_changes = staged_config.has_changes()

    return ConfigDiffResponse(
        diff=diff,
        has_changes=has_changes,
        in_config_mode=True,
    )


@config_router.post("/set", response_model=ConfigSetResponse)
async def config_set(
    request: Request,
    body: ConfigSetRequest,
) -> ConfigSetResponse:
    """Stage a configuration change.

    This endpoint wraps the existing config_set tool handler.
    Only available in configuration mode.

    Args:
        body: Request with path and value to set

    Returns:
        Response with staged status and validation result
    """
    from ploston_core.config.tools.config_set import handle_config_set

    # Check mode
    mode_manager = getattr(request.app.state, "mode_manager", None)
    if mode_manager and not mode_manager.is_configuration_mode():
        raise HTTPException(
            status_code=400,
            detail="config/set only available in configuration mode. "
            "Use 'configure' tool to enter configuration mode.",
        )

    # Get staged config
    staged_config = getattr(request.app.state, "staged_config", None)
    if not staged_config:
        raise HTTPException(status_code=503, detail="Staged config not available")

    # Call the handler
    result = await handle_config_set(
        {"path": body.path, "value": body.value},
        staged_config,
    )

    return ConfigSetResponse(
        staged=result.get("staged", False),
        path=result.get("path"),
        error=result.get("error"),
        validation=result.get("validation"),
    )


@config_router.post("/done", response_model=ConfigDoneResponse)
async def config_done(request: Request) -> ConfigDoneResponse:
    """Apply staged configuration changes.

    This endpoint wraps the existing config_done tool handler.
    Only available in configuration mode.

    Returns:
        Response with success status and applied config info
    """
    from ploston_core.config.tools.config_done import handle_config_done

    # Check mode
    mode_manager = getattr(request.app.state, "mode_manager", None)
    if mode_manager and not mode_manager.is_configuration_mode():
        raise HTTPException(
            status_code=400,
            detail="config/done only available in configuration mode. "
            "Use 'configure' tool to enter configuration mode.",
        )

    # Get required dependencies from app state
    staged_config = getattr(request.app.state, "staged_config", None)
    if not staged_config:
        raise HTTPException(status_code=503, detail="Staged config not available")

    config_loader = getattr(request.app.state, "config_loader", None)
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    write_location = getattr(request.app.state, "config_write_location", None)
    redis_store = getattr(request.app.state, "redis_store", None)
    runner_registry = getattr(request.app.state, "runner_registry", None)

    # Call the handler
    result = await handle_config_done(
        {},  # No arguments needed
        staged_config,
        config_loader,
        mode_manager,
        mcp_manager,
        write_location,
        redis_store,
        runner_registry,
    )

    return ConfigDoneResponse(
        success=result.get("success", False),
        mode=result.get("mode", "configuration"),
        config_written_to=result.get("config_written_to"),
        errors=result.get("errors"),
        capabilities=result.get("capabilities"),
        message=result.get("message"),
    )
