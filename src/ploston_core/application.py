"""Ploston Application - orchestrator for all components.

This is the main application class that initializes and wires all
components together. It is the equivalent of AELApplication from
the original AEL codebase.

Both ploston (OSS) and ploston-enterprise use this class for
proper component initialization.
"""

import os
import sys
from typing import TYPE_CHECKING, Any, TextIO

from ploston_core.config import ConfigLoader, MCPHTTPConfig
from ploston_core.engine import WorkflowEngine
from ploston_core.errors import ErrorFactory, ErrorRegistry
from ploston_core.invoker import SandboxFactory, ToolInvoker
from ploston_core.logging import AELLogger, LogConfig
from ploston_core.mcp import MCPClientManager
from ploston_core.mcp_frontend import MCPFrontend, MCPServerConfig
from ploston_core.registry import ToolRegistry
from ploston_core.sandbox import SandboxConfig
from ploston_core.telemetry import OTLPExporterConfig, TelemetryConfig, setup_telemetry
from ploston_core.template import TemplateEngine
from ploston_core.types import MCPTransport
from ploston_core.workflow import WorkflowRegistry

if TYPE_CHECKING:
    from ploston_core.config.redis_store import RedisConfigStore
    from ploston_core.runner_management import RunnerRegistry


class PlostApplication:
    """
    Ploston Application orchestrator.

    Initializes and wires all components together. This class handles
    the full initialization sequence including:

    1. Config loading
    2. Logger setup
    3. Telemetry setup
    4. Error registry
    5. MCP client manager (connects to MCP servers)
    6. Tool registry (discovers tools from MCP servers)
    7. Workflow registry (loads workflows from config)
    8. Sandbox factory (Python execution)
    9. Template engine (Jinja2)
    10. Tool invoker
    11. Workflow engine
    12. MCP frontend (server)
    """

    def __init__(
        self,
        config_path: str | None = None,
        log_output: TextIO | None = None,
        transport: MCPTransport = MCPTransport.HTTP,
        http_host: str = "0.0.0.0",
        http_port: int = 8080,
        with_rest_api: bool = True,
        rest_api_prefix: str = "/api/v1",
        rest_api_docs: bool = True,
    ):
        """Initialize application.

        Args:
            config_path: Path to config file (optional)
            log_output: Output stream for logs (default: sys.stdout)
            transport: MCP transport type (default: HTTP for server mode)
            http_host: HTTP host (default: 0.0.0.0)
            http_port: HTTP port (default: 8080)
            with_rest_api: Enable REST API alongside MCP server (default: True)
            rest_api_prefix: URL prefix for REST API (default: /api/v1)
            rest_api_docs: Enable OpenAPI docs at /docs (default: True)
        """
        self._config_path = config_path
        self._log_output = log_output or sys.stdout
        self._transport = transport
        self._http_host = http_host
        self._http_port = http_port
        self._with_rest_api = with_rest_api
        self._rest_api_prefix = rest_api_prefix
        self._rest_api_docs = rest_api_docs
        self._initialized = False

        # Components (initialized in initialize())
        self.config_loader: ConfigLoader | None = None
        self.config: Any = None
        self.logger: AELLogger | None = None
        self.error_registry: ErrorRegistry | None = None
        self.error_factory: ErrorFactory | None = None
        self.mcp_manager: MCPClientManager | None = None
        self.tool_registry: ToolRegistry | None = None
        self.workflow_registry: WorkflowRegistry | None = None
        self.sandbox_factory: SandboxFactory | None = None
        self.template_engine: TemplateEngine | None = None
        self.tool_invoker: ToolInvoker | None = None
        self.workflow_engine: WorkflowEngine | None = None
        self.mcp_frontend: MCPFrontend | None = None
        self.redis_config_store: "RedisConfigStore | None" = None
        self.runner_registry: "RunnerRegistry | None" = None

    async def initialize(self) -> None:
        """Initialize all components.

        This method performs the full initialization sequence:
        1. Load configuration
        2. Set up logging
        3. Set up telemetry
        4. Create error handling
        5. Connect to MCP servers
        6. Discover tools
        7. Load workflows
        8. Set up execution environment
        9. Create MCP frontend
        """
        if self._initialized:
            return

        # 1. Config Loader
        self.config_loader = ConfigLoader()
        self.config = self.config_loader.load(self._config_path)

        # 2. Logger
        log_config = LogConfig(
            level=self.config.logging.level,
            format=self.config.logging.format,
            show_params=self.config.logging.options.show_params,
            show_results=self.config.logging.options.show_results,
            truncate_at=self.config.logging.options.truncate_at,
            components={
                "workflow": self.config.logging.components.workflow,
                "step": self.config.logging.components.step,
                "tool": self.config.logging.components.tool,
                "sandbox": self.config.logging.components.sandbox,
            },
            output=self._log_output,
        )
        self.logger = AELLogger(log_config)

        # 3. Telemetry Setup
        telemetry_enabled = (
            os.environ.get("PLOSTON_TELEMETRY_ENABLED", "").lower() == "true"
            or self.config.telemetry.enabled
        )
        metrics_enabled = (
            os.environ.get("PLOSTON_METRICS_ENABLED", "").lower() == "true"
            or self.config.telemetry.metrics.enabled
        )
        traces_enabled = (
            os.environ.get("PLOSTON_TRACES_ENABLED", "").lower() == "true"
            or self.config.telemetry.tracing.enabled
        )
        logs_enabled = (
            os.environ.get("PLOSTON_LOGS_ENABLED", "").lower() == "true"
            or self.config.telemetry.logging.enabled
        )

        otlp_endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            self.config.telemetry.export.otlp.endpoint,
        )
        otlp_enabled = bool(otlp_endpoint) and (traces_enabled or logs_enabled)

        telemetry_config = TelemetryConfig(
            enabled=telemetry_enabled,
            service_name=os.environ.get("OTEL_SERVICE_NAME", self.config.telemetry.service_name),
            service_version=self.config.telemetry.service_version,
            metrics_enabled=metrics_enabled,
            traces_enabled=traces_enabled,
            traces_sample_rate=self.config.telemetry.tracing.sample_rate,
            logs_enabled=logs_enabled,
            otlp=OTLPExporterConfig(
                enabled=otlp_enabled,
                endpoint=otlp_endpoint,
                insecure=os.environ.get("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true",
                protocol=self.config.telemetry.export.otlp.protocol,
                headers=self.config.telemetry.export.otlp.headers,
            ),
        )
        setup_telemetry(telemetry_config)

        # 4. Error Registry & Factory
        self.error_registry = ErrorRegistry()
        self.error_factory = ErrorFactory(self.error_registry)

        # 5. MCP Client Manager
        self.mcp_manager = MCPClientManager(
            self.config.tools,
            logger=self.logger,
        )

        # 5b. Redis Config Store & Runner Registry (if Redis URL is configured)
        redis_url = os.environ.get("PLOSTON_REDIS_URL") or os.environ.get("AEL_REDIS_URL")
        if redis_url:
            from ploston_core.config.redis_store import (
                RedisConfigStore,
                RedisConfigStoreOptions,
            )
            from ploston_core.runner_management import PersistentRunnerRegistry

            redis_options = RedisConfigStoreOptions(redis_url=redis_url)
            self.redis_config_store = RedisConfigStore(redis_options)
            await self.redis_config_store.connect()
            self.runner_registry = PersistentRunnerRegistry(self.redis_config_store)
            await self.runner_registry.load_from_redis()
            if self.logger:
                from ploston_core.types.enums import LogLevel

                self.logger._log(
                    LogLevel.INFO,
                    "runner",
                    "Runner registry initialized with Redis persistence",
                    {"redis_url": redis_url.split("@")[-1]},  # Hide credentials
                )

        # 6. Tool Registry
        self.tool_registry = ToolRegistry(
            self.mcp_manager,
            self.config.tools,
            logger=self.logger,
        )
        await self.tool_registry.initialize()

        # 7. Workflow Registry
        self.workflow_registry = WorkflowRegistry(
            self.tool_registry,
            self.config.workflows,
            logger=self.logger,
        )
        await self.workflow_registry.initialize()

        # 8. Sandbox Factory
        sandbox_config = SandboxConfig(
            timeout=self.config.python_exec.timeout,
            max_tool_calls=self.config.python_exec.max_tool_calls,
            allowed_imports=self.config.python_exec.default_imports,
        )
        self.sandbox_factory = SandboxFactory(
            sandbox_config,
            logger=self.logger,
        )

        # 9. Template Engine
        self.template_engine = TemplateEngine()

        # 10. Tool Invoker
        self.tool_invoker = ToolInvoker(
            self.tool_registry,
            self.mcp_manager,
            self.sandbox_factory,
            logger=self.logger,
            error_factory=self.error_factory,
        )

        # 11. Workflow Engine
        self.workflow_engine = WorkflowEngine(
            self.workflow_registry,
            self.tool_invoker,
            self.template_engine,
            self.config.execution,
            logger=self.logger,
            error_factory=self.error_factory,
        )

        # 12. MCP Frontend
        http_config = None
        if self._transport == MCPTransport.HTTP:
            http_config = MCPHTTPConfig(
                host=self._http_host,
                port=self._http_port,
            )

        # Create REST API app if dual-mode is enabled
        rest_app = None
        if self._with_rest_api and self._transport == MCPTransport.HTTP:
            from ploston_core.api.app import create_rest_app
            from ploston_core.api.config import RESTConfig

            rest_config = RESTConfig(
                prefix="",  # Prefix is handled by mount point
                docs_enabled=self._rest_api_docs,
                docs_path="/docs",
                redoc_path="/redoc",
                openapi_path="/openapi.json",
            )
            rest_app = create_rest_app(
                workflow_registry=self.workflow_registry,
                workflow_engine=self.workflow_engine,
                tool_registry=self.tool_registry,
                tool_invoker=self.tool_invoker,
                config=rest_config,
                logger=self.logger,
                runner_registry=self.runner_registry,
            )

        # Create mode manager in RUNNING mode
        from ploston_core.config import Mode, ModeManager, StagedConfig
        from ploston_core.config.tools import ConfigToolRegistry

        mode_manager = ModeManager(initial_mode=Mode.RUNNING)

        # Create config tool registry
        staged_config = StagedConfig(self.config_loader)
        config_tool_registry = ConfigToolRegistry(
            staged_config=staged_config,
            config_loader=self.config_loader,
            mode_manager=mode_manager,
            mcp_manager=self.mcp_manager,
        )

        self.mcp_frontend = MCPFrontend(
            self.workflow_engine,
            self.tool_registry,
            self.workflow_registry,
            self.tool_invoker,
            config=MCPServerConfig(),
            logger=self.logger,
            mode_manager=mode_manager,
            config_tool_registry=config_tool_registry,
            transport=self._transport,
            http_config=http_config,
            rest_app=rest_app,
            rest_prefix=self._rest_api_prefix,
        )

        self._initialized = True

    async def shutdown(self) -> None:
        """Shutdown all components."""
        if not self._initialized:
            return

        # Disconnect MCP clients
        if self.mcp_manager:
            await self.mcp_manager.disconnect_all()

        self._initialized = False

    def start_watching(self) -> None:
        """Start watching for config/workflow changes (placeholder for hot-reload)."""
        pass

    def stop_watching(self) -> None:
        """Stop watching for changes (placeholder for hot-reload)."""
        pass

    async def run_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> Any:
        """Run workflow by ID.

        Args:
            workflow_id: Workflow ID
            inputs: Workflow inputs
            timeout_seconds: Optional timeout

        Returns:
            ExecutionResult
        """
        if not self.workflow_engine:
            raise RuntimeError("Application not initialized")

        return await self.workflow_engine.execute(workflow_id, inputs, timeout_seconds)

    async def start(self) -> None:
        """Start the MCP frontend server.

        This is a convenience method that initializes the application
        (if not already initialized) and starts the server.
        """
        if not self._initialized:
            await self.initialize()

        if self.mcp_frontend:
            await self.mcp_frontend.start()
