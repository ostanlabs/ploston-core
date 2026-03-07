"""AEL REST API module."""

from ploston_core.api.app import create_rest_app
from ploston_core.api.config import APIKeyConfig, RESTConfig

__all__ = [
    "create_rest_app",
    "RESTConfig",
    "APIKeyConfig",
]
