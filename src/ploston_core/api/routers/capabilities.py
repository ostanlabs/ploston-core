"""Capabilities endpoint for tier detection.

GET /api/v1/capabilities returns server capabilities.
CLI uses this to determine available features.
"""

from fastapi import APIRouter

from ploston_core.extensions.capabilities import get_capabilities_provider

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


@router.get("/capabilities")
async def get_capabilities() -> dict:
    """
    Get server capabilities.
    
    Returns tier information, enabled features, and limits.
    CLI uses this to determine which commands are available.
    
    Returns:
        dict: Server capabilities including:
            - tier: "community" or "enterprise"
            - version: Server version
            - features: Dict of enabled features
            - limits: Dict of resource limits
            - license: License info (enterprise only)
    """
    provider = get_capabilities_provider()
    capabilities = provider.get_capabilities()
    return capabilities.to_dict()

