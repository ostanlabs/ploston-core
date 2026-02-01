"""REST API application factory."""

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ploston_core.api.config import RESTConfig
from ploston_core.api.errors import setup_error_handlers
from ploston_core.api.middleware import (
    APIKeyAuthMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)
from ploston_core.api.routers import (
    capabilities_router,
    config_router,
    execution_router,
    health_router,
    runner_router,
    runner_static_router,
    tool_router,
    workflow_router,
)
from ploston_core.api.store import ExecutionStore, InMemoryExecutionStore, SQLiteExecutionStore

if TYPE_CHECKING:
    from ploston_core.config.mode_manager import ModeManager
    from ploston_core.config.models import AELConfig
    from ploston_core.invoker import ToolInvoker
    from ploston_core.logging import AELLogger
    from ploston_core.registry import ToolRegistry
    from ploston_core.runner_management import RunnerRegistry
    from ploston_core.workflow import WorkflowEngine, WorkflowRegistry


def create_rest_app(
    workflow_registry: "WorkflowRegistry",
    workflow_engine: "WorkflowEngine",
    tool_registry: "ToolRegistry",
    tool_invoker: "ToolInvoker",
    config: RESTConfig,
    logger: "AELLogger | None" = None,
    runner_registry: "RunnerRegistry | None" = None,
    ael_config: "AELConfig | None" = None,
    mode_manager: "ModeManager | None" = None,
) -> FastAPI:
    """Create FastAPI application with all routes.

    Args:
        workflow_registry: Registry for workflow definitions
        workflow_engine: Engine for executing workflows
        tool_registry: Registry for available tools
        tool_invoker: Invoker for tool calls
        config: REST API configuration
        logger: Optional AEL logger
        runner_registry: Optional runner registry for runner management
        ael_config: Optional AEL configuration for pre-configured runners
        mode_manager: Optional mode manager for config/running mode tracking

    Returns:
        Configured FastAPI application
    """
    # Create FastAPI app
    app = FastAPI(
        title=config.title,
        version=config.version,
        docs_url=config.docs_path if config.docs_enabled else None,
        redoc_url=config.redoc_path if config.docs_enabled else None,
        openapi_url=config.openapi_path if config.docs_enabled else None,
    )

    # Store dependencies in app state
    app.state.workflow_registry = workflow_registry
    app.state.workflow_engine = workflow_engine
    app.state.tool_registry = tool_registry
    app.state.tool_invoker = tool_invoker
    app.state.config = config
    app.state.logger = logger
    app.state.runner_registry = runner_registry
    app.state.ael_config = ael_config
    app.state.mode_manager = mode_manager

    # Create execution store
    if config.execution_store_sqlite_path:
        app.state.execution_store: ExecutionStore = SQLiteExecutionStore(
            config.execution_store_sqlite_path
        )
    else:
        app.state.execution_store = InMemoryExecutionStore(
            max_records=config.execution_store_max_records
        )

    # Add middleware (order matters - first added is outermost)
    # Request ID middleware (always enabled)
    app.add_middleware(RequestIDMiddleware)

    # CORS middleware
    if config.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Rate limiting middleware
    if config.rate_limiting_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=config.requests_per_minute,
        )

    # API key authentication middleware
    if config.require_auth:
        app.add_middleware(
            APIKeyAuthMiddleware,
            api_keys=config.api_keys,
        )

    # Setup error handlers
    setup_error_handlers(app)

    # Include routers
    app.include_router(capabilities_router)  # No prefix - already has /api/v1
    app.include_router(config_router, prefix=config.prefix)
    app.include_router(health_router, prefix=config.prefix)
    app.include_router(workflow_router, prefix=config.prefix)
    app.include_router(execution_router, prefix=config.prefix)
    app.include_router(tool_router, prefix=config.prefix)
    app.include_router(runner_router, prefix=config.prefix)

    # Runner static endpoints (no prefix - /runner/*)
    app.include_router(runner_static_router)

    return app
