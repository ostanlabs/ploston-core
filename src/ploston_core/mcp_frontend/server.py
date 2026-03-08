"""MCP Frontend - AEL as MCP server."""

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from ploston_core.api.routers import is_runner_connected, send_tool_call_to_runner
from ploston_core.config import MCPHTTPConfig, Mode, ModeManager
from ploston_core.engine import WorkflowEngine
from ploston_core.errors import AELError, create_error
from ploston_core.errors.errors import ErrorCategory
from ploston_core.invoker import ToolInvoker
from ploston_core.logging import AELLogger
from ploston_core.mcp_frontend.http_transport import bridge_context
from ploston_core.registry import ToolRegistry
from ploston_core.runner_management.registry import RunnerRegistry
from ploston_core.runner_management.router import parse_tool_prefix
from ploston_core.telemetry import ChainDetector, instrument_tool_call, record_tool_result
from ploston_core.telemetry.context import direct_execution_id
from ploston_core.telemetry.store.types import (
    ErrorRecord as TelemetryErrorRecord,
)
from ploston_core.telemetry.store.types import (
    ExecutionStatus as TelemetryExecutionStatus,
)
from ploston_core.telemetry.store.types import (
    ExecutionType,
)
from ploston_core.types import ExecutionStatus, LogLevel, MCPTransport
from ploston_core.types.internal import InternalToolSource

if TYPE_CHECKING:
    from ploston.workflow import WorkflowRegistry

from .http_transport import HTTPTransport
from .stdio import read_message, write_message
from .types import MCPServerConfig

logger = logging.getLogger(__name__)


def _split_tool_name(qualified_name: str) -> tuple[str, str]:
    """Split a qualified tool name into (bridge, tool_name).

    ``obsidian-mcp__read_docs`` → ``("obsidian-mcp", "read_docs")``

    If the name does not contain ``__``, *bridge* is returned as an empty
    string and *tool_name* is the original name.
    """
    if "__" in qualified_name:
        bridge, _, tool = qualified_name.partition("__")
        return bridge, tool
    return "", qualified_name


class MCPFrontend:
    """
    AEL as MCP server.

    Exposes:
    - All registered tools (passthrough)
    - All registered workflows (as workflow_* tools)

    Transport: stdio (default) or HTTP
    """

    def __init__(
        self,
        workflow_engine: WorkflowEngine,
        tool_registry: ToolRegistry,
        workflow_registry: "WorkflowRegistry",
        tool_invoker: ToolInvoker,
        config: MCPServerConfig | None = None,
        logger: AELLogger | None = None,
        mode_manager: ModeManager | None = None,
        config_tool_registry: Any | None = None,
        transport: MCPTransport = MCPTransport.STDIO,
        http_config: MCPHTTPConfig | None = None,
        rest_app: Any | None = None,
        rest_prefix: str = "/api/v1",
        chain_detector: ChainDetector | None = None,
        runner_registry: RunnerRegistry | None = None,
        workflow_tools: Any | None = None,
        telemetry_collector: Any | None = None,
    ):
        """Initialize MCP frontend.

        Args:
            workflow_engine: Workflow engine for executing workflows
            tool_registry: Tool registry for listing tools
            workflow_registry: Workflow registry for listing workflows
            tool_invoker: Tool invoker for executing tools
            config: MCP server configuration
            logger: Logger instance
            mode_manager: Mode manager for tracking configuration/running mode
            config_tool_registry: Registry for config tools (ael:config_*)
            transport: Transport type (stdio or http)
            http_config: HTTP transport configuration (required if transport is http)
            rest_app: Optional FastAPI app to mount for REST API (dual-mode)
            rest_prefix: URL prefix for REST API (default: /api/v1)
            chain_detector: Optional chain detector for detecting tool sequences
            runner_registry: Optional runner registry for routing tools to runners (DEC-123)
            workflow_tools: Optional WorkflowToolsProvider for workflow CRUD tools
            telemetry_collector: Optional TelemetryCollector for execution lifecycle (DEC-152)
        """
        self._workflow_engine = workflow_engine
        self._tool_registry = tool_registry
        self._workflow_registry = workflow_registry
        self._tool_invoker = tool_invoker
        self._config = config or MCPServerConfig()
        self._logger = logger
        self._running = False
        self._transport = transport
        self._http_config = http_config or MCPHTTPConfig()
        self._http_transport: HTTPTransport | None = None
        self._rest_app = rest_app
        self._rest_prefix = rest_prefix

        # Mode management
        self._mode_manager = mode_manager or ModeManager()
        self._config_tool_registry = config_tool_registry

        # Chain detection (T-446)
        self._chain_detector = chain_detector

        # Runner routing (DEC-123)
        self._runner_registry = runner_registry

        # Workflow CRUD tools
        self._workflow_tools = workflow_tools

        # Telemetry collector for execution lifecycle (Tier 3 — DEC-152)
        self._telemetry_collector = telemetry_collector

        # Register for mode change notifications
        self._mode_manager.on_mode_change(self._on_mode_change)

    async def start(self) -> None:
        """Start MCP server.

        For stdio: Reads JSON-RPC messages from stdin, writes to stdout.
        For HTTP: Starts HTTP server with /mcp and /mcp/sse endpoints.
        """
        self._running = True

        if self._transport == MCPTransport.HTTP:
            await self._start_http()
        else:
            await self._start_stdio()

    async def _start_stdio(self) -> None:
        """Start MCP server on stdio transport."""
        while self._running:
            message = await read_message()
            if message is None:
                break

            response = await self._handle_message(message)
            if response:
                await write_message(response)

    async def _start_http(self) -> None:
        """Start MCP server on HTTP transport.

        If rest_app is provided, mounts it for dual-mode operation (MCP + REST API).
        """
        import uvicorn

        self._http_transport = HTTPTransport(
            message_handler=self._handle_message,
            host=self._http_config.host,
            port=self._http_config.port,
            cors_origins=self._http_config.cors_origins,
            tls_enabled=self._http_config.tls.enabled,
            tls_cert_file=self._http_config.tls.cert_file,
            tls_key_file=self._http_config.tls.key_file,
            rest_app=self._rest_app,
            rest_prefix=self._rest_prefix,
        )
        self._http_transport.start()

        config = uvicorn.Config(
            self._http_transport.app,
            host=self._http_config.host,
            port=self._http_config.port,
            log_level="info",
        )

        # Add TLS if enabled
        if self._http_config.tls.enabled:
            config.ssl_certfile = self._http_config.tls.cert_file
            config.ssl_keyfile = self._http_config.tls.key_file

        server = uvicorn.Server(config)
        await server.serve()

    async def stop(self) -> None:
        """Stop MCP server."""
        self._running = False
        if self._http_transport:
            self._http_transport.stop()

    def _on_mode_change(self, new_mode: Mode) -> None:
        """Send tools/list_changed notification when mode changes.

        Args:
            new_mode: The new mode
        """
        asyncio.create_task(self._send_tools_changed_notification())

    async def _send_tools_changed_notification(self) -> None:
        """Send MCP notification that tools list has changed."""
        notification = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        if self._transport == MCPTransport.HTTP and self._http_transport:
            await self._http_transport.send_notification(notification)
        else:
            await write_message(notification)

    async def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Route message to appropriate handler.

        Args:
            message: JSON-RPC message

        Returns:
            JSON-RPC response or None (None for notifications)
        """
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        # JSON-RPC 2.0: Notifications (no id) should not receive a response
        is_notification = "id" not in message

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "ping":
                result = {"pong": True}
            elif method and method.startswith("notifications/"):
                # MCP notifications - no response needed
                return None
            else:
                # Unknown method - only respond if it's a request (has id)
                if is_notification:
                    return None
                return self._error_response(msg_id, -32601, f"Method not found: {method}")

            # Only respond to requests, not notifications
            if is_notification:
                return None
            return self._success_response(msg_id, result)

        except AELError as e:
            if is_notification:
                return None
            error_data: dict[str, Any] = {"code": e.code}
            if e.detail:
                error_data["detail"] = e.detail
            if e.suggestion:
                error_data["suggestion"] = e.suggestion
            return self._error_response(
                msg_id,
                e.http_status,
                e.message,
                error_data,
            )
        except Exception as e:
            if is_notification:
                return None
            # Log full traceback server-side for debugging
            logger.exception("Unhandled exception in MCP message handler")
            # Return structured error so agents/users can report it
            import traceback

            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            short_tb = "".join(tb_lines[-3:]).strip()  # last 3 frames
            return self._error_response(
                msg_id,
                500,
                f"Internal server error: {type(e).__name__}: {e}",
                {
                    "code": "INTERNAL_ERROR",
                    "detail": f"An unexpected error occurred while processing the request. "
                    f"Error type: {type(e).__name__}, message: {e}",
                    "traceback_tail": short_tb,
                },
            )

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle initialize request.

        Args:
            params: Initialize parameters

        Returns:
            Initialize response
        """
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": self._config.name,
                "version": self._config.version,
            },
            "capabilities": {
                "tools": {"listChanged": True},
            },
        }

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request with mode awareness.

        Includes runner tools with prefix per DEC-123 (e.g., "runner-name:tool").

        Supports optional source filtering via params:
        - sources: list of sources to include (e.g., ["native", "runner"])
          Valid values: "mcp", "native", "system", "runner"
          If not provided, returns all tools.

        Args:
            params: List parameters

        Returns:
            Tools list response
        """
        tools = []

        # Parse source filter from params
        source_filter = params.get("sources") if params else None
        include_runner = True
        include_mcp = True
        include_native = True
        include_system = True

        if source_filter:
            # Convert string sources to internal sources
            include_runner = "runner" in source_filter
            include_mcp = "mcp" in source_filter
            include_native = "native" in source_filter
            include_system = "system" in source_filter

        if self._mode_manager.mode == Mode.CONFIGURATION:
            # Configuration mode: only config tools
            if self._config_tool_registry:
                tools = self._config_tool_registry.get_for_mcp_exposure()
        else:
            # Running mode: all tools + workflows + configure + runner tools
            if self._config.expose_tools:
                # Build list of sources to include
                sources_to_include = []
                if include_mcp:
                    sources_to_include.append(InternalToolSource.MCP)
                if include_native:
                    sources_to_include.append(InternalToolSource.NATIVE)
                if include_system:
                    sources_to_include.append(InternalToolSource.SYSTEM)

                # Get tools filtered by source (or all if no filter)
                filter_sources = sources_to_include if source_filter else None
                for tool in self._tool_registry.get_for_mcp_exposure(filter_sources):
                    tools.append(tool)

            # Only include workflows if no source filter (workflows don't have a source)
            if self._config.expose_workflows and not source_filter:
                for workflow in self._workflow_registry.get_for_mcp_exposure():
                    tools.append(workflow)

            # Add configure and read-only ploston: tools (only if no source filter)
            if self._config_tool_registry and not source_filter:
                configure_tool = self._config_tool_registry.get_configure_tool_for_mcp_exposure()
                if configure_tool:
                    tools.append(configure_tool)
            # Add workflow CRUD tools (workflow_schema, workflow_list, etc.)
            if self._workflow_tools and not source_filter:
                for tool in self._workflow_tools.get_for_mcp_exposure():
                    tools.append(tool)

            # Add runner tools with prefix (DEC-123)
            # Format: runner__mcp__toolname (e.g., mac__fs__read_file)
            # Runner reports tools as mcp__toolname, we add runner__ prefix
            if self._runner_registry and include_runner:
                for runner in self._runner_registry.list():
                    if runner.status.value == "connected" and runner.available_tools:
                        for tool_info in runner.available_tools:
                            # Tool info can be a string or a dict with name/description
                            # Tool name from runner is already prefixed: mcp__toolname
                            if isinstance(tool_info, str):
                                tool_name = tool_info
                                tool_desc = f"Tool from runner '{runner.name}'"
                                tool_schema = {}
                            else:
                                tool_name = tool_info.get("name", str(tool_info))
                                tool_desc = tool_info.get(
                                    "description", f"Tool from runner '{runner.name}'"
                                )
                                tool_schema = tool_info.get("inputSchema", {})

                            # Prefix with runner name using __ delimiter
                            # Result: runner__mcp__toolname
                            prefixed_name = f"{runner.name}__{tool_name}"
                            tools.append(
                                {
                                    "name": prefixed_name,
                                    "description": tool_desc,
                                    "inputSchema": tool_schema,
                                }
                            )

        return {"tools": tools}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request with mode awareness and runner routing.

        Implements prefix-based routing per DEC-123:
        - "runner__mcp__tool" -> Route to runner via WebSocket
        - "tool" -> Route to CP (local MCP servers)

        Args:
            params: Call parameters

        Returns:
            Tool call response
        """
        name = params.get("name")
        arguments = params.get("arguments", {})

        if not name:
            raise create_error("PARAM_INVALID", message="Tool name is required")

        # Config tools (ael:* and ploston:* namespace)
        if name.startswith("ael:") or name.startswith("ploston:") or name == "configure":
            return await self._handle_config_tool_call(name, arguments)

        # Check for runner prefix (DEC-123: runner__mcp__tool)
        runner_name, mcp_name, actual_tool = parse_tool_prefix(name)
        if runner_name:
            # Pass mcp__tool to runner (it knows how to route internally)
            tool_for_runner = f"{mcp_name}__{actual_tool}" if mcp_name else actual_tool
            return await self._execute_runner_tool(runner_name, tool_for_runner, arguments)

        # Workflow calls (CRUD tools + execution)
        if name.startswith("workflow_"):
            if self._mode_manager.mode == Mode.CONFIGURATION:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="Workflows not available in configuration mode. Call config_done first.",
                    tool_name=name,
                    http_status=503,
                )
            # Route CRUD tools (workflow_schema, workflow_list, etc.) before execution
            if self._workflow_tools:
                from ploston_core.workflow.tools import WorkflowToolsProvider

                if WorkflowToolsProvider.is_crud_tool(name):
                    return await self._workflow_tools.call(name, arguments)
            workflow_id = name[len("workflow_") :]
            return await self._execute_workflow(workflow_id, arguments)

        # Regular tool calls
        if self._mode_manager.mode == Mode.CONFIGURATION:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message="Tools not available in configuration mode. Call config_done first.",
                tool_name=name,
                http_status=503,
            )
        return await self._execute_tool(name, arguments)

    async def _handle_config_tool_call(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle config tool calls with mode awareness.

        Args:
            name: Tool name (ael:*)
            arguments: Tool arguments

        Returns:
            Tool call response
        """
        if not self._config_tool_registry:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Config tool {name} not available (no config tool registry)",
                tool_name=name,
                http_status=503,
            )

        if self._mode_manager.mode == Mode.CONFIGURATION:
            # In config mode, configure is not available
            if name == "configure":
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="configure only available in running mode",
                    tool_name=name,
                    http_status=503,
                )
            return await self._config_tool_registry.call(name, arguments)
        else:
            # In running mode, only configure is available from config tools
            _running_mode_tools = {"configure", "ploston:configure"}
            if name in _running_mode_tools:
                return await self._config_tool_registry.call(name, arguments)
            else:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message=f"{name} only available in configuration mode. Call configure first.",
                    tool_name=name,
                    http_status=503,
                )

    async def _execute_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute workflow and return MCP response.

        Args:
            workflow_id: Workflow ID
            inputs: Workflow inputs

        Returns:
            MCP response
        """
        result = await self._workflow_engine.execute(workflow_id, inputs)

        if result.status == ExecutionStatus.COMPLETED:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result.outputs),
                    }
                ],
                "isError": False,
            }
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": result.error.message if result.error else "Workflow failed",
                    }
                ],
                "isError": True,
            }

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute tool directly and return MCP response.

        Args:
            tool_name: Tool name
            arguments: Tool arguments

        Returns:
            MCP response
        """
        execution_id: str | None = None
        ctx_token = None

        # Start telemetry record for direct tool call (DEC-050 / DEC-152)
        if self._telemetry_collector:
            try:
                _bctx = bridge_context.get()
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=tool_name,
                    source="mcp",
                    session_id=_bctx.bridge_id if _bctx else None,
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        try:
            start_ms = int(time.time() * 1000)
            bridge, short_tool = _split_tool_name(tool_name)

            # START event
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "direct",
                    f"Direct tool call: {tool_name}",
                    {
                        "source": "tool",
                        "event": "direct_tool_called",
                        "tool_name": short_tool,
                        "bridge": bridge,
                    },
                )

            result = await self._tool_invoker.invoke(tool_name, arguments)
            duration_ms = int(time.time() * 1000) - start_ms

            # RESULT events
            if self._logger:
                if result.success:
                    self._logger._log(
                        LogLevel.INFO,
                        "direct",
                        f"Direct tool completed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_completed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                        },
                    )
                else:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                            "error": result.error.message if result.error else "unknown",
                            "error_type": result.error.code if result.error else "UNKNOWN",
                        },
                    )

            # Chain detection (T-446 / DEC-057): Process tool call for chain detection
            # Only for direct tool calls (not workflows)
            if self._chain_detector and result.success and not tool_name.startswith("workflow_"):
                try:
                    await self._chain_detector.process_tool_call(
                        tool_name=tool_name,
                        params=arguments,
                        result=result.output,
                        bridge_id=bridge or None,
                    )
                except Exception:
                    # Chain detection is non-critical - don't fail the tool call
                    pass

            # End telemetry record (DEC-152)
            if self._telemetry_collector and execution_id:
                try:
                    if result.success:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.COMPLETED,
                            outputs={"output": str(result.output)[:500]} if result.output else None,
                        )
                    else:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code=result.error.code if result.error else "UNKNOWN",
                                category="tool",
                                message=result.error.message if result.error else "unknown",
                            ),
                        )
                except Exception:
                    pass

            if result.success:
                response: dict[str, Any] = {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                json.dumps(result.output)
                                if not isinstance(result.output, str)
                                else result.output
                            ),
                        }
                    ],
                    "isError": False,
                }
                # Include structuredContent if available (required by MCP spec when outputSchema is defined)
                if result.structured_content is not None:
                    response["structuredContent"] = result.structured_content
                return response
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": result.error.message if result.error else "Tool call failed",
                        }
                    ],
                    "isError": True,
                }
        finally:
            if ctx_token is not None:
                direct_execution_id.reset(ctx_token)

    async def _execute_runner_tool(
        self,
        runner_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute tool on a runner via WebSocket (DEC-123).

        Routes tool calls to runners based on prefix:
        - "runner-name:tool" -> Route to runner via WebSocket

        Args:
            runner_name: Name of the runner to route to
            tool_name: Tool name (without prefix)
            arguments: Tool arguments

        Returns:
            MCP response
        """
        if not self._runner_registry:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message="Runner routing not configured (no runner registry)",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=503,
            )

        # Find runner by name
        runner = self._runner_registry.get_by_name(runner_name)
        if not runner:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Runner '{runner_name}' not found",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=404,
            )

        # Check if runner is connected
        if not is_runner_connected(runner.id):
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Runner '{runner_name}' is not connected",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=503,
            )

        # Instrument runner tool calls for telemetry
        # tool_name is already mcp__actual_tool at this point (runner prefix stripped above)
        # source = runner.name so dashboards can filter/group by runner identity
        # Extract bridge context for distributed topology labels (DEC-142)
        _bctx = bridge_context.get()
        _bridge_id = _bctx.bridge_id if _bctx else None

        # Telemetry lifecycle for runner tool calls (DEC-152)
        execution_id: str | None = None
        ctx_token = None
        if self._telemetry_collector:
            try:
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=tool_name,
                    source="runner",
                    caller_id=runner.name,
                    session_id=_bridge_id,
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        start_ms = int(time.time() * 1000)
        bridge, short_tool = _split_tool_name(tool_name)
        async with instrument_tool_call(
            tool_name,  # "obsidian-mcp__list_files" — NOT prefixed with runner
            source=runner.name,  # human-readable runner name as tool_source
            runner_id=runner.name,  # human-readable name, NOT runner.id (UUID)
            bridge_id=_bridge_id,
        ) as telemetry_result:
            try:
                # START event
                if self._logger:
                    self._logger._log(
                        LogLevel.INFO,
                        "direct",
                        f"Direct runner tool call: {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_called",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                        },
                    )

                result = await send_tool_call_to_runner(
                    runner_id=runner.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    timeout=60.0,
                )

                duration_ms = int(time.time() * 1000) - start_ms

                # Result from runner is in format: {"output": ..., "error": ...}
                # or {"content": [...], "isError": ...} if runner returns MCP format
                is_error = False
                if "content" in result:
                    # Runner returned MCP format directly
                    is_error = result.get("isError", False)
                    record_tool_result(telemetry_result, success=not is_error)
                    if self._logger:
                        if is_error:
                            self._logger._log(
                                LogLevel.ERROR,
                                "direct",
                                f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                                {
                                    "source": "tool",
                                    "event": "direct_tool_failed",
                                    "tool_name": short_tool,
                                    "bridge": bridge,
                                    "runner_id": runner.name,
                                    "duration_ms": duration_ms,
                                    "error": "runner returned error",
                                    "error_type": "TOOL_FAILED",
                                },
                            )
                        else:
                            self._logger._log(
                                LogLevel.INFO,
                                "direct",
                                f"Direct runner tool completed ({duration_ms}ms): {tool_name}",
                                {
                                    "source": "tool",
                                    "event": "direct_tool_completed",
                                    "tool_name": short_tool,
                                    "bridge": bridge,
                                    "runner_id": runner.name,
                                    "duration_ms": duration_ms,
                                },
                            )
                    # Chain detection for MCP-format runner results (DEC-057)
                    if self._chain_detector and not is_error:
                        try:
                            # Extract text content from MCP content blocks
                            chain_output = ""
                            for block in result.get("content", []):
                                if block.get("type") == "text":
                                    chain_output += block.get("text", "")
                            await self._chain_detector.process_tool_call(
                                tool_name=tool_name,
                                params=arguments,
                                result=chain_output,
                                runner_id=runner.name,
                                bridge_id=_bridge_id,
                            )
                        except Exception:
                            pass  # Chain detection is non-critical
                    # End telemetry for MCP-format result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            if is_error:
                                await self._telemetry_collector.end_execution(
                                    execution_id=execution_id,
                                    status=TelemetryExecutionStatus.FAILED,
                                    error=TelemetryErrorRecord(
                                        code="TOOL_FAILED",
                                        category="tool",
                                        message="runner returned error",
                                    ),
                                )
                            else:
                                await self._telemetry_collector.end_execution(
                                    execution_id=execution_id,
                                    status=TelemetryExecutionStatus.COMPLETED,
                                )
                            execution_id = None  # Prevent double-end in finally
                        except Exception:
                            pass
                    return result
                elif "error" in result and result.get("error"):
                    record_tool_result(telemetry_result, success=False, error_code="TOOL_FAILED")
                    if self._logger:
                        self._logger._log(
                            LogLevel.ERROR,
                            "direct",
                            f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                            {
                                "source": "tool",
                                "event": "direct_tool_failed",
                                "tool_name": short_tool,
                                "bridge": bridge,
                                "runner_id": runner.name,
                                "duration_ms": duration_ms,
                                "error": str(result["error"]),
                                "error_type": "TOOL_FAILED",
                            },
                        )
                    # End telemetry for error result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            await self._telemetry_collector.end_execution(
                                execution_id=execution_id,
                                status=TelemetryExecutionStatus.FAILED,
                                error=TelemetryErrorRecord(
                                    code="TOOL_FAILED",
                                    category="tool",
                                    message=str(result["error"]),
                                ),
                            )
                            execution_id = None
                        except Exception:
                            pass
                    return {
                        "content": [{"type": "text", "text": str(result["error"])}],
                        "isError": True,
                    }
                else:
                    output = result.get("output", result)
                    record_tool_result(telemetry_result, success=True)
                    if self._logger:
                        self._logger._log(
                            LogLevel.INFO,
                            "direct",
                            f"Direct runner tool completed ({duration_ms}ms): {tool_name}",
                            {
                                "source": "tool",
                                "event": "direct_tool_completed",
                                "tool_name": short_tool,
                                "bridge": bridge,
                                "runner_id": runner.name,
                                "duration_ms": duration_ms,
                            },
                        )
                    # Chain detection for output-format runner results (DEC-057)
                    if self._chain_detector:
                        try:
                            await self._chain_detector.process_tool_call(
                                tool_name=tool_name,
                                params=arguments,
                                result=output,
                                runner_id=runner.name,
                                bridge_id=_bridge_id,
                            )
                        except Exception:
                            pass  # Chain detection is non-critical
                    # End telemetry for successful output result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            await self._telemetry_collector.end_execution(
                                execution_id=execution_id,
                                status=TelemetryExecutionStatus.COMPLETED,
                                outputs={"output": str(output)[:500]} if output else None,
                            )
                            execution_id = None
                        except Exception:
                            pass
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    json.dumps(output) if not isinstance(output, str) else output
                                ),
                            }
                        ],
                        "isError": False,
                    }

            except TimeoutError:
                duration_ms = int(time.time() * 1000) - start_ms
                if self._logger:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                            "duration_ms": duration_ms,
                            "error": f"Tool call to runner '{runner_name}' timed out",
                            "error_type": "TimeoutError",
                        },
                    )
                raise AELError(
                    code="TOOL_TIMEOUT",
                    category=ErrorCategory.TOOL,
                    message=f"Tool call to runner '{runner_name}' timed out",
                    tool_name=f"{runner_name}:{tool_name}",
                    http_status=504,
                )
            except AELError:
                raise
            except Exception as e:
                duration_ms = int(time.time() * 1000) - start_ms
                if self._logger:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                            "duration_ms": duration_ms,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                logger.exception(f"Error routing tool to runner: {e}")
                raise AELError(
                    code="TOOL_EXECUTION_FAILED",
                    category=ErrorCategory.TOOL,
                    message=f"Tool call to runner '{runner_name}' failed: {e}",
                    tool_name=f"{runner_name}:{tool_name}",
                    http_status=500,
                )
            finally:
                # End any dangling telemetry execution (DEC-152)
                if self._telemetry_collector and execution_id:
                    try:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code="TOOL_EXECUTION_FAILED",
                                category="tool",
                                message="Execution ended without explicit completion",
                            ),
                        )
                    except Exception:
                        pass
                if ctx_token is not None:
                    direct_execution_id.reset(ctx_token)

    def _success_response(self, msg_id: Any, result: Any) -> dict[str, Any]:
        """Build success response.

        Args:
            msg_id: Message ID
            result: Result data

        Returns:
            JSON-RPC success response
        """
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    def _error_response(
        self,
        msg_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> dict[str, Any]:
        """Build error response.

        Args:
            msg_id: Message ID
            code: Error code
            message: Error message
            data: Additional error data

        Returns:
            JSON-RPC error response
        """
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": error,
        }
