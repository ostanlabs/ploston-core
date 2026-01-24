"""REST API routers."""

from .capabilities import router as capabilities_router
from .config import config_router
from .executions import execution_router
from .health import health_router
from .tools import tool_router
from .workflows import workflow_router

__all__ = [
    "capabilities_router",
    "config_router",
    "health_router",
    "workflow_router",
    "execution_router",
    "tool_router",
]
