"""Config router for Ploston API."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

config_router = APIRouter(prefix="/config", tags=["config"])


class ConfigDiffResponse(BaseModel):
    """Response for config diff endpoint."""

    diff: str
    has_changes: bool
    in_config_mode: bool


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
