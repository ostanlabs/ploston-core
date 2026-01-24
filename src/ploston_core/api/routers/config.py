"""Config router for Ploston API."""

from typing import Any

from fastapi import APIRouter, Query, Request

config_router = APIRouter(prefix="/config", tags=["config"])


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
